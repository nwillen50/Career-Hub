"""Regression tests for bugs a second independent review found (CH-79); each failed first."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Router
from careerhub.llm.cache import ResponseCache
from careerhub.llm.fake import FakeProvider
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.jobs.filters import assess_filters
from careerhub.modules.jobs.scoring import ranked_jobs, score_job
from careerhub.modules.jobs.tailoring import build_request, resume_for_model
from careerhub.modules.plan import IcsCalendar, Interval, PlanTask, plan_week
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.redact import redact_contacts
from careerhub.profile.rubric import Criterion, Rubric

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        (r"john\_smith@gmail.com", "smith"),
        (r"j\_s@u.edu", "u.edu"),
        ("linkedin.com/in/john-smith-123", "john-smith"),
        ("github.com/jsmith", "jsmith"),
        ("johnsmith.com/work", "johnsmith"),
        (r"\href{tel:+15551234567}{call}", "5551234567"),
        (r"\href{mailto:john.smith@gmail.com}{write}", "gmail"),
        ("(617) 555-0142", "555"),
        ("617.555.0142", "0142"),
        ("+44 20 7946 0958", "7946"),
    ],
)
def test_contact_details_do_not_survive_in_realistic_forms(text: str, gone: str) -> None:
    assert gone not in resume_for_model(text)


@pytest.mark.parametrize(
    "text",
    [
        "GPA 3.9 2022--2026",
        "2019 2020 2021 2022 2023",
        "Built 120 tools in 3 years",
        "Node.js and Vue.js",
    ],
)
def test_ordinary_numbers_and_tech_names_are_kept(text: str) -> None:
    assert redact_contacts(text) == text


def test_the_users_name_and_profile_contacts_are_removed_from_the_prompt() -> None:
    doc = ProfileDoc(
        name="Jane Q Sample", skills=["Python"], about="Email me: jane@x.org or 617-555-0142"
    )
    job = Job(title="Engineer", raw_text="text")
    job.company = type("C", (), {"name": "Acme"})()  # type: ignore[assignment]
    resume = resume_for_model(r"\textbf{Jane Sample} built a tool. Jane led it.", doc.name)
    prompt = build_request(job, doc, resume).prompt
    assert "Jane" not in prompt and "Sample" not in prompt
    assert "jane@x.org" not in prompt and "555-0142" not in prompt and "[name]" in prompt


RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
DOC = ProfileDoc(name="Jane", skills=["Python"])


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def one_job(session: Session) -> Job:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Engineer", "u", None, "Text")])
    job = session.scalars(select(Job)).one()
    assess_filters(session, job, HardConstraints())
    return job


def test_an_unscorable_answer_is_retried_and_never_cached(session: Session) -> None:
    job = one_job(session)
    wrong = json.dumps({"criteria": [{"name": "other", "score": 3}], "explanation": "x"})
    right = json.dumps({"criteria": [{"name": "field", "score": 7}], "explanation": "y"})
    provider = FakeProvider("m", replies=[wrong, right])
    cache = ResponseCache(
        session.get_bind()
        and __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(
            bind=session.get_bind()
        )
    )
    router = Router([provider], cache=cache)
    assessment = score_job(session, router, job, DOC, RUBRIC, ["m"])
    assert assessment is not None and assessment.fit_score == 70.0
    assert len(provider.requests) == 2  # the retry fixed it; the wrong answer is not replayed


def test_two_copies_of_a_job_are_shown_once_when_the_original_closes(session: Session) -> None:
    a = Source(kind="greenhouse", name="a", url="a", status="approved")
    b = Source(kind="greenhouse", name="b", url="b", status="approved")
    c = Source(kind="greenhouse", name="c", url="c", status="approved")
    session.add_all([a, b, c])
    session.commit()
    now = datetime(2026, 10, 21, tzinfo=UTC)
    for source, ext in ((a, "1"), (b, "2"), (c, "3")):
        store_jobs(
            session, source, [NormalizedJob("Acme", ext, "Engineer", "u", "Boston", "T")], now
        )
    reply = json.dumps({"criteria": [{"name": "field", "score": 5}], "explanation": "ok"})
    router = Router([FakeProvider("m", replies=[reply])])
    for job in session.scalars(select(Job)):
        assess_filters(session, job, HardConstraints())
        score_job(session, router, job, DOC, RUBRIC, ["m"])
    store_jobs(session, a, [], now)  # the original closes
    assert len(ranked_jobs(session)) == 1


def test_a_small_budget_does_not_make_every_task_unplaceable() -> None:
    free = [
        Interval(datetime(2026, 10, d, 13, tzinfo=UTC), datetime(2026, 10, d, 20, tzinfo=UTC))
        for d in (21, 22)
    ]
    plan = plan_week([PlanTask("long", 90)], free, budget_minutes=120, zone=NY)
    assert len(plan.blocks) == 1


MALFORMED = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//EN
BEGIN:VEVENT
UID:bad
DTSTAMP:20261001T000000Z
SUMMARY:Broken SECRET
END:VEVENT
BEGIN:VEVENT
UID:good
DTSTAMP:20261001T000000Z
DTSTART:20261021T140000Z
DTEND:20261021T150000Z
END:VEVENT
END:VCALENDAR
"""


def test_a_malformed_event_is_skipped_not_a_crash() -> None:
    start, end = datetime(2026, 10, 21, tzinfo=UTC), datetime(2026, 10, 22, tzinfo=UTC)
    busy = IcsCalendar(MALFORMED, NY).busy(start, end)
    assert busy == [
        Interval(datetime(2026, 10, 21, 14, tzinfo=UTC), datetime(2026, 10, 21, 15, tzinfo=UTC))
    ]
