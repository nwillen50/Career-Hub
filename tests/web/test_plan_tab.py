"""The Plan tab (CH-143): the weekly plan on its own page, suggestions only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job  # noqa: F401  (saved-data helper)

from careerhub.core.migrate import migrate
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings, save_settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.web.app import create_app
from careerhub.web.plan_view import gather_plan, sources_used

TOKEN = "t"
PORT = 8765
# A Monday morning in New York (UTC-4 in October): working hours still lie ahead.
MONDAY = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
BUSY_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1
DTSTART:20261019T150000Z
DTEND:20261019T210000Z
SUMMARY:Secret meeting title
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def test_with_ranked_jobs_the_plan_is_grouped_by_day_within_the_budget(session: Session) -> None:
    for n in range(1, 4):
        add_job(session, n, 90 - n)
    view = gather_plan(session, MONDAY)
    assert view.days and view.planned_minutes > 0
    assert view.planned_minutes <= view.budget_minutes == 300
    assert view.days[0].heading.startswith("Monday, Oct 19")
    assert all(b.minutes > 0 for day in view.days for b in day.blocks)
    assert view.used.endswith("of 5h planned")
    assert "Working hours only (no calendar is connected)" in view.sources


def test_a_busy_calendar_file_is_avoided_and_its_titles_never_shown(
    session: Session, tmp_path: Path
) -> None:
    calendar = tmp_path / "work.ics"
    calendar.write_text(BUSY_ICS, encoding="utf-8")
    save_settings(Settings(calendar_files=(str(calendar),)))
    for n in range(1, 4):
        add_job(session, n, 90 - n)
    view = gather_plan(session, MONDAY)
    assert "Calendar file work.ics" in view.sources
    starts = [b.start for day in view.days for b in day.blocks if day.heading.startswith("Monday")]
    assert all(not ("11:00" <= start < "17:00") for start in starts)  # 15:00-21:00Z is 11-17 local
    html = web(session).get("/plan").text
    assert "Secret meeting title" not in html and str(tmp_path) not in html


def test_an_unreadable_calendar_is_a_problem_in_words_not_an_error(
    session: Session, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.ics"
    broken.write_text("this is not a calendar", encoding="utf-8")
    save_settings(Settings(calendar_files=(str(broken),)))
    add_job(session, 1, 80)
    view = gather_plan(session, MONDAY)
    assert view.problems and "could not be read" in view.problems[0] and not view.days
    html = web(session).get("/plan")
    assert html.status_code == 200 and 'role="alert"' in html.text


def test_apple_calendar_when_on_is_named_and_a_failure_is_shown(session: Session) -> None:
    save_settings(Settings(use_apple_calendar=True))
    add_job(session, 1, 80)
    view = gather_plan(session, MONDAY)
    assert "Apple Calendar (free and busy times only)" in view.sources
    assert view.problems  # off a Mac, EventKit is unavailable: said in words, plan still made


def test_no_jobs_shows_the_empty_state(session: Session) -> None:
    html = web(session).get("/plan").text
    assert "Nothing to plan yet." in html and "Suggestions only" in html
    assert "<h1>Plan</h1>" in html and 'href="/plan" aria-current="page"' in html


def test_the_page_is_read_only_and_passes_the_accessibility_checks(session: Session) -> None:
    add_job(session, 1, 80)
    c = web(session)
    html = c.get("/plan").text
    assert check_page(html) == [] and "<form" in html  # only the end-session form
    assert html.count("<form") == 1


def test_sources_text_lists_files_by_name_only() -> None:
    names = sources_used(Settings(calendar_files=("/home/me/private/cal.ics",)))
    assert names == ["Working hours", "Calendar file cal.ics"]
