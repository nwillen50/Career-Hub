"""Tests for reviewing and using extracted facts, and for the extraction eval (CH-110)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Job, ProfileExtraction, Source
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_database, open_engine, session_factory
from careerhub.llm import Router
from careerhub.llm.fake import FakeProvider
from careerhub.modules.jobs import NormalizedJob, scoring, store_jobs, tailoring
from careerhub.profile.evals import load_cases, run_profile_eval
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import UnknownExtraction, accepted_facts, decide, facts_text, pending
from careerhub.profile.models import Education, ProfileDoc
from careerhub.profile.profile import format_profile, load_profile
from careerhub.profile.rubric import Criterion, Rubric

RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
DOC = ProfileDoc(
    name="Jane Sample",
    education=[Education(school="Example University", degree="BS CS")],
    skills=["Python"],
)
FACTS = {
    "experiences": [{"role": "Intern", "organization": "Acme", "points": ["Built a tool"]}],
    "education": [{"school": "Example University", "degree": "Wrong Degree"}],
    "courses": [{"code": "CS 101", "title": "Intro", "grade": "A"}],
    "gpa": 3.65,
    "skills": ["python", "SQL"],
}


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def add_extraction(
    session: Session, kind: str = "resume", facts: dict[str, Any] | None = None
) -> ProfileExtraction:
    count = len(session.scalars(select(ProfileExtraction)).all())
    row = ProfileExtraction(
        kind=kind,
        file_sha256=f"{count:064d}",
        prompt_version=1,
        facts=facts or FACTS,
        status="pending",
    )
    session.add(row)
    session.commit()
    return row


def make_job(session: Session) -> Job:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Engineer", "u", "Boston", "Text")])
    return session.scalars(select(Job)).one()


def test_pending_facts_never_reach_prompts_but_accepted_ones_do(session: Session) -> None:
    row = add_extraction(session)
    job = make_job(session)
    assert accepted_facts(session) is None
    before = scoring.build_request(job, DOC, RUBRIC, accepted_facts(session)).prompt
    assert "Intern at Acme" not in before
    decide(session, row.id, accept=True)
    after = scoring.build_request(job, DOC, RUBRIC, accepted_facts(session)).prompt
    assert "Intern at Acme" in after and "GPA: 3.65" in after and "CS 101 Intro (A)" in after
    tailored = tailoring.build_request(job, DOC, "resume text", accepted_facts(session)).prompt
    assert "Intern at Acme" in tailored


def test_what_the_user_typed_wins_over_extracted_facts(session: Session) -> None:
    row = add_extraction(session)
    decide(session, row.id, accept=True)
    text = facts_text(accepted_facts(session), DOC)
    assert "Wrong Degree" not in text  # the user typed that school, so theirs stands
    assert "More skills: SQL" in text  # "python" is already typed in profile.md
    prompt = scoring.profile_text(DOC, accepted_facts(session))
    assert "BS CS, Example University" in prompt and "Wrong Degree" not in prompt


def test_accepted_facts_are_redacted_like_everything_else(session: Session) -> None:
    facts = {"experiences": [{"role": "Intern", "points": ["Emailed jane@x.org; Jane led it"]}]}
    decide(session, add_extraction(session, facts=facts).id, accept=True)
    prompt = scoring.profile_text(DOC, accepted_facts(session))
    assert "jane@x.org" not in prompt and "Jane" not in prompt


def test_rejecting_keeps_facts_out_and_unknown_numbers_are_errors(session: Session) -> None:
    row = add_extraction(session)
    decide(session, row.id, accept=False)
    assert accepted_facts(session) is None and pending(session) == []
    with pytest.raises(UnknownExtraction):
        decide(session, row.id, accept=True)  # already decided
    with pytest.raises(UnknownExtraction):
        decide(session, 999, accept=True)


def test_the_newest_accepted_extraction_per_kind_is_used(session: Session) -> None:
    old = add_extraction(session, facts={"skills": ["Old"]})
    new = add_extraction(session, facts={"skills": ["New"]})
    decide(session, old.id, accept=True)
    decide(session, new.id, accept=True)
    assert accepted_facts(session).skills == ["New"]  # type: ignore[union-attr]


def test_changed_accepted_facts_change_the_scoring_input_hash(session: Session) -> None:
    job = make_job(session)
    before = scoring.input_hash(job, DOC, None)
    decide(session, add_extraction(session).id, accept=True)
    assert scoring.input_hash(job, DOC, accepted_facts(session)) != before


def test_the_facts_commands_list_accept_and_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    engine = open_database(store)
    migrate(engine)
    with session_factory(engine)() as session:
        add_extraction(session)
        add_extraction(session, kind="transcript", facts={"gpa": 3.0})
    assert main(["profile", "facts"], store) == 0
    out = capsys.readouterr().out
    assert "#1 from your resume" in out and "Experience: Intern at Acme" in out
    assert main(["profile", "facts", "accept", "1"], store) == 0
    assert main(["profile", "facts", "reject", "2"], store) == 0
    assert main(["profile", "facts", "accept", "1"], store) == 1  # already decided
    capsys.readouterr()
    main(["profile", "facts"], store)
    assert "No facts are waiting" in capsys.readouterr().out
    assert main(["profile", "facts", "accept"], store) == 2


def test_profile_show_reports_counts_only(session: Session, tmp_path: Path) -> None:
    decide(session, add_extraction(session).id, accept=True)
    add_extraction(session, kind="transcript", facts={"gpa": 3.0})
    shown = format_profile(load_profile(session))
    assert "1 waiting for review, 1 accepted" in shown and "Acme" not in shown


SAMPLE = Path(__file__).parents[2] / "evals" / "profile" / "sample.yaml"
PERFECT = {
    "resume-intern": {
        "experiences": [
            {"role": "SWE Intern", "organization": "Acme Robotics"},
            {"role": "TA", "organization": "Example University"},
        ],
        "education": [{"school": "Example University"}],
        "skills": ["python", "SQL", "Git"],
    },
    "transcript-basic": {
        "education": [{"school": "Example University"}],
        "courses": [
            {"code": "CS 101", "title": "Intro"},
            {"code": "MATH 220", "title": "Linear Algebra"},
        ],
        "gpa": 3.65,
    },
}


def test_the_eval_scores_a_perfect_reading_at_100_percent() -> None:
    cases = load_cases(SAMPLE)
    replies = [json.dumps(PERFECT[c.id]) for c in cases]
    report = run_profile_eval(Router([FakeProvider("m", replies=replies)]), cases, ["m"])
    assert all(v == 1.0 for v in report.accuracy.values()) and report.misses == []


def test_the_eval_reports_misses_and_counts_invalid_answers_as_wrong() -> None:
    cases = load_cases(SAMPLE)
    partial = json.dumps({"skills": ["Python"], "education": [{"school": "Example University"}]})
    report = run_profile_eval(
        Router([FakeProvider("m", replies=[partial, "not json"])]), cases, ["m"]
    )
    assert report.accuracy["skills"] == pytest.approx(1 / 3)
    assert report.accuracy["gpa"] == 0.0 and report.accuracy["course_codes"] == 0.0
    assert "transcript-basic gpa" in report.misses and "Missed:" in report.summary()


def test_a_malformed_eval_file_is_a_clear_error(tmp_path: Path) -> None:
    from careerhub.profile.evals import ProfileEvalError

    (tmp_path / "bad.yaml").write_text("cases: [{id: x}]")
    with pytest.raises(ProfileEvalError):
        load_cases(tmp_path / "bad.yaml")
    assert isinstance(ProfileFacts(), ProfileFacts)
