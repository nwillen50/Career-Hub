"""The System status tab (CH-144). Read-only; saved data; no keys, prompts, or profile text."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from careerhub.core.companies import get_or_create_company, suggest_merges
from careerhub.core.migrate import migrate
from careerhub.core.models import LLMUsage, Source, utcnow
from careerhub.core.runlog import finish_run, start_run
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings, save_settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm.providers import api_key_name
from careerhub.web.app import create_app
from careerhub.web.status_view import MAX_RUNS, gather_status

TOKEN = "t"
PORT = 8765
SECRET = "sk-super-secret-key-value"


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session, store: InMemoryKeyStore | None = None) -> TestClient:
    app = create_app(
        TOKEN,
        PORT,
        lambda: True,
        session_factory(session.get_bind()),
        store or InMemoryKeyStore(),
        lambda: False,
    )
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def test_empty_system_shows_every_section_with_an_empty_state(session: Session) -> None:
    html = web(session).get("/status").text
    for phrase in (
        "No runs yet.",
        "No sources yet.",
        "$0.00 of $20.00 this month",
        "none yet (run hub models add)",
        "Scoring is off until the profile is ready.",
        "0 proposed job sources",
        "0 company merge suggestions",
    ):
        assert phrase in html, phrase
    assert check_page(html) == []


def test_runs_sources_spending_and_waiting_items(session: Session) -> None:
    ok = start_run(session, "nightly")
    finish_run(session, ok, 3)
    bad = start_run(session, "nightly")
    finish_run(session, bad, 0, ConnectionError())
    session.add_all(
        [
            Source(
                kind="greenhouse",
                name="Acme",
                url="u1",
                status="approved",
                health="ok",
                last_fetched_at=utcnow() - timedelta(hours=2),
            ),
            Source(kind="lever", name="Globex", url="u2", status="proposed"),
            Source(
                kind="manual", name="Added by you", url="manual://added-by-you", status="approved"
            ),
            LLMUsage(provider="p", model="m", cost_usd=1.25),
        ]
    )
    get_or_create_company(session, "Acme")
    get_or_create_company(session, "Acme Robotics")
    session.commit()
    suggest_merges(session)
    html = web(session).get("/status").text
    assert "ConnectionError" in html and "succeeded" in html and "failed" in html
    assert "Acme" in html and "Globex" in html and "Added by you" not in html
    assert "$1.25 of $20.00 this month" in html
    assert "1 proposed job source:" in html and "1 company merge suggestion:" in html


def test_only_the_latest_runs_are_listed(session: Session) -> None:
    for _ in range(MAX_RUNS + 4):
        finish_run(session, start_run(session, "nightly"))
    assert (
        len(gather_status(session, InMemoryKeyStore(), local_running=lambda: False).runs)
        == MAX_RUNS
    )


def test_switched_on_models_and_routes_are_listed_and_the_key_is_never_shown(
    session: Session,
) -> None:
    store = InMemoryKeyStore()
    store.set(api_key_name("claude-haiku"), SECRET)
    html = web(session, store).get("/status").text
    assert "claude-haiku" in html and "extract" in html and "score" in html
    assert SECRET not in html and "sk-" not in html


def test_the_budget_comes_from_settings(session: Session) -> None:
    save_settings(Settings(monthly_budget_usd=7.5))
    assert "of $7.50 this month" in web(session).get("/status").text


def test_a_models_problem_is_shown_not_raised(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("keychain locked")

    monkeypatch.setattr("careerhub.web.status_view.with_catalog", broken)
    html = web(session).get("/status").text
    assert "Models could not be read (RuntimeError)." in html and 'role="alert"' in html
    assert "keychain locked" not in html


def test_hostile_names_are_escaped_and_reading_changes_nothing(session: Session) -> None:
    session.add(
        Source(kind="greenhouse", name="<script>alert(1)</script>", url="u", status="approved")
    )
    session.commit()
    c = web(session)
    html = c.get("/status").text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert session.query(Source).count() == 1 and html.count("<form") == 1


def test_run_and_fetch_times_are_local_not_utc(session: Session) -> None:
    """Bug CH-193: System status showed run and fetch times in UTC."""
    save_settings(Settings(timezone="Asia/Tokyo"))
    run = start_run(session, "fetch")
    finish_run(session, run, "ok")
    view = gather_status(session, InMemoryKeyStore(), local_running=lambda: False)
    assert view.runs and "UTC" not in view.runs[0].started
