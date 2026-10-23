"""The Today page: top jobs, follow-ups, deadlines, last run (CH-139). Read-only; saved data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Company, Interaction, Job, Person, Source, utcnow
from careerhub.core.runlog import finish_run, start_run
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.web.app import create_app
from careerhub.web.today import MAX_FOLLOW_UPS, MAX_TOP_JOBS, clean_section, gather_today

TOKEN = "t"
PORT = 8765


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def add_job(
    session: Session,
    n: int,
    score: float,
    company: str = "Acme",
    deadline: str | None = None,
    salary: tuple[int, int] | None = None,
    url: str | None = None,
    title: str | None = None,
) -> Job:
    source = session.query(Source).first()
    if source is None:
        source = Source(kind="greenhouse", name="s", url="u", status="approved")
        session.add(source)
        session.flush()
    firm = session.query(Company).filter_by(name=company).first() or Company(
        name=company, name_key=company.lower()
    )
    fields: dict[str, object] = {"deadline": deadline}
    if salary:
        fields.update(salary_min_usd=salary[0], salary_max_usd=salary[1])
    job = Job(
        company=firm,
        source_id=source.id,
        external_id=str(n),
        title=title or f"Role {n}",
        url=url or f"https://example.com/{n}",
        location="Boston, MA",
        dedupe_key=f"k{n}",
        first_seen_at=utcnow(),
        last_seen_at=utcnow(),
        structured_fields={"fields": fields},
    )
    session.add(job)
    session.flush()
    session.add(
        Assessment(
            job_id=job.id,
            filter_passed=True,
            fit_score=score,
            explanation="Strong match. It fits.",
            criteria_scores=[{"name": "field", "score": 9, "reason": "Right field"}],
            model_used="m",
            prompt_version=1,
            rubric_version=1,
        )
    )
    session.commit()
    return job


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def test_at_most_five_top_jobs_best_first_with_the_wireframe_headline(session: Session) -> None:
    for n in range(1, 8):
        add_job(session, n, 90 - n, salary=(120000, 150000) if n == 1 else None)
    html = web(session).get("/today").text
    assert "Top jobs (5)" in html and html.count('class="card job"') == MAX_TOP_JOBS
    assert html.index("Role 1") < html.index("Role 2") and "Role 6" not in html
    assert "Boston, MA" in html and "$120,000 to $150,000" in html
    assert "Fit score <strong>89</strong> out of 100" in html
    assert "<details>" in html  # details are folded away by default
    assert "<details open" not in html


def test_the_page_passes_the_accessibility_checks(session: Session) -> None:
    add_job(session, 1, 80)
    assert check_page(web(session).get("/today").text) == []


def test_hostile_posting_text_is_escaped_and_unsafe_links_are_dropped(session: Session) -> None:
    add_job(
        session,
        1,
        80,
        company='"><img src=x onerror=alert(1)>',
        url="javascript:alert(1)",
        title="<script>alert(2)</script>",
    )
    html = web(session).get("/today").text
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "javascript:" not in html and "&lt;script&gt;" in html


def test_empty_state_scoring_off_and_failed_run(session: Session) -> None:
    html = web(session).get("/today").text
    assert "Nothing ranked yet" in html and 'href="/status"' in html
    assert "No follow-ups due." in html and "No deadlines this week." in html
    assert "Scoring starts when your profile is ready" in html  # the readiness checklist shows
    run = start_run(session, "nightly")
    finish_run(session, run, 0, ConnectionError())
    html = web(session).get("/today").text
    assert 'role="alert"' in html and "The last nightly run failed" in html


def test_follow_ups_due_overdue_and_the_more_link(session: Session) -> None:
    now = utcnow()
    firm = Company(name="Globex", name_key="globex")
    person = Person(full_name="Ben Example", company=firm)
    session.add_all([firm, person])
    session.flush()
    for i in range(MAX_FOLLOW_UPS + 3):
        session.add(
            Interaction(
                person_id=person.id,
                occurred_at=now - timedelta(days=20),
                channel="email",
                summary=f"note {i}",
                follow_up_due=now - timedelta(days=i + 1),
            )
        )
    session.add(  # not due yet: tomorrow
        Interaction(
            person_id=person.id,
            occurred_at=now,
            channel="call",
            follow_up_due=now + timedelta(days=2),
        )
    )
    session.commit()
    data = gather_today(session, now)
    assert len(data.follow_ups) == MAX_FOLLOW_UPS and data.more_overdue == 3
    assert data.follow_ups[0].overdue_days >= data.follow_ups[-1].overdue_days
    html = web(session).get("/today").text
    assert "Follow-ups due (5)" in html and "+3 more overdue" in html
    assert "Follow up with Ben Example at Globex" in html and 'href="/tracker"' in html


def test_deadlines_this_week_only(session: Session) -> None:
    today = utcnow().date()
    add_job(session, 1, 90, deadline=(today + timedelta(days=2)).isoformat())
    add_job(session, 2, 85, deadline=(today + timedelta(days=30)).isoformat())
    add_job(session, 3, 80, deadline=(today - timedelta(days=1)).isoformat())
    html = web(session).get("/today").text
    block = html.split("Deadlines this week", 1)[1]
    assert "Role 1" in block and "Role 2" not in block and "Role 3" not in block


def test_last_successful_run_is_shown(session: Session) -> None:
    run = start_run(session, "nightly")
    finish_run(session, run, 1)
    assert "Last successful run:" in web(session).get("/today").text


def test_one_section_at_a_time_is_remembered(session: Session) -> None:
    add_job(session, 1, 80)
    c = web(session)
    only = c.get("/today?section=followups")
    assert "Follow-ups due" in only.text and "Top jobs (" not in only.text
    assert "ch_today_section=followups" in only.headers["set-cookie"]
    again = c.get("/today")  # no query: the cookie decides
    assert "Follow-ups due" in again.text and "Top jobs (" not in again.text
    assert "Top jobs (1)" in c.get("/today?section=all").text
    assert clean_section("<script>") == "all" and clean_section(None) == "all"


def test_exploration_says_it_is_not_ready_yet(session: Session) -> None:
    html = web(session).get("/today?section=exploration").text
    assert "Exploration picks start once Career Hub has learned" in html


def test_nothing_is_changed_by_reading_today(session: Session) -> None:
    add_job(session, 1, 80)
    before = utcnow()
    c = web(session)
    c.get("/today")
    assert session.query(Job).count() == 1 and session.query(Interaction).count() == 0
    assert datetime.now(UTC) >= before


def test_the_database_error_page_still_shows_when_the_database_cannot_open(
    session: Session,
) -> None:
    app = create_app(TOKEN, PORT, lambda: False, session_factory(session.get_bind()))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    assert c.get("/today").status_code == 503


def test_today_is_the_users_day_not_the_utc_day(session: Session) -> None:
    """Review: at 9pm in New York it is already tomorrow in UTC; follow-ups must not jump ahead."""
    evening = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)  # Oct 22, 9pm in New York
    firm = Company(name="Globex", name_key="globex")
    person = Person(full_name="Ben Example", company=firm)
    session.add_all([firm, person])
    session.flush()
    for label, due in (
        ("tonight", datetime(2026, 10, 23, 2, 0, tzinfo=UTC)),  # Oct 22, 10pm: due today
        ("tomorrow", datetime(2026, 10, 23, 14, 0, tzinfo=UTC)),  # Oct 23, 10am: not yet
    ):
        session.add(
            Interaction(
                person_id=person.id, occurred_at=evening, channel="call", summary=label,
                follow_up_due=due,
            )
        )  # fmt: skip
    session.commit()
    data = gather_today(session, evening)
    assert [f.summary for f in data.follow_ups] == ["tonight"]
    assert data.local_now is not None and data.local_now.day == 22


def test_a_deadline_shows_even_if_the_job_is_not_one_of_the_five_cards(session: Session) -> None:
    """Review: deadlines came only from the five shown cards."""
    today = utcnow().date()
    for n in range(1, 8):
        add_job(session, n, 90 - n)
    low = add_job(session, 8, 10, deadline=(today + timedelta(days=2)).isoformat())
    edge = add_job(session, 9, 9, deadline=(today + timedelta(days=7)).isoformat())
    html = web(session).get("/today").text
    block = html.split("Deadlines this week", 1)[1]
    assert low.title in block and edge.title not in block  # a 7-day window: today to today+6
