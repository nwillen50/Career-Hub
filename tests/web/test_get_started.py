"""The Get started page (CH-203): each step's check, the redirect before setup is done, skip."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import ProfileExtraction, RunLog, Source
from careerhub.core.runlog import finish_run, start_run
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm.providers import api_key_name
from careerhub.modules.jobs.targets import Target, save_targets
from careerhub.profile.readiness import set_scoring
from careerhub.web.app import create_app
from careerhub.web.setup_view import gather_setup

TOKEN = "t"
PORT = 8765
SECRET = "sk-super-secret-key-value"
PROFILE = "---\nname: Jane Sample\nskills: [Python]\ninterests: [robotics]\n---\n"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


@pytest.fixture
def session(data: Path, tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session, store: InMemoryKeyStore | None) -> TestClient:
    app = create_app(
        TOKEN, PORT, lambda: True, session_factory(session.get_bind()), store, lambda: False
    )
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)


def finish_setup(session: Session, data: Path, store: InMemoryKeyStore) -> None:
    """Do every required step with saved sample data."""
    store.set(api_key_name("claude-haiku"), SECRET)
    for name, text in (
        ("profile/profile.md", PROFILE),
        ("profile/preferences.yaml", "hard_constraints:\n  locations: ['Boston, MA']\n"),
        ("inputs/resume/resume.txt", "Built a tool."),
    ):
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_text(text)
    set_scoring(True, date(2026, 10, 20))
    save_targets([Target(name="Acme", enabled=True)])
    session.add(Source(kind="greenhouse", name="Acme", url="u", status="approved"))
    session.commit()
    finish_run(session, start_run(session, "nightly"), 3)


def test_a_fresh_install_has_only_the_database_step_done(session: Session) -> None:
    view = gather_setup(session, InMemoryKeyStore(), lambda: False)
    assert [s.done for s in view.steps] == [True, False, False, False, False, False, False]
    assert not view.complete and view.next_step is not None and view.next_step.number == 2
    assert all(s.fix for s in view.steps)


def test_every_step_turns_done_with_its_own_check(session: Session, data: Path) -> None:
    store = InMemoryKeyStore()
    finish_setup(session, data, store)
    view = gather_setup(session, store, lambda: False)
    assert [s.done for s in view.steps][:7] == [True] * 7, [
        (s.number, s.detail) for s in view.steps
    ]
    assert view.complete and view.next_step is None


def test_pending_facts_keep_step_4_open_no_source_keeps_6_open_and_a_failed_run_is_not_a_night(
    session: Session, data: Path
) -> None:
    store = InMemoryKeyStore()
    finish_setup(session, data, store)
    session.add(
        ProfileExtraction(
            kind="resume", file_sha256="a" * 64, prompt_version=2, status="pending", facts={}
        )
    )
    session.commit()
    steps = {s.number: s for s in gather_setup(session, store, lambda: False).steps}
    assert not steps[4].done and "waiting for your review" in steps[4].detail
    session.query(Source).delete()
    session.commit()
    assert not {s.number: s for s in gather_setup(session, store, lambda: False).steps}[6].done
    session.query(RunLog).delete()
    finish_run(session, start_run(session, "nightly"), 0, ConnectionError())
    assert not {s.number: s for s in gather_setup(session, store, lambda: False).steps}[7].done


def test_opening_with_the_link_lands_on_get_started_until_setup_is_done(session: Session) -> None:
    c = web(session, InMemoryKeyStore())
    first = c.get(f"/?t={TOKEN}")
    assert first.status_code == 303 and first.headers["location"] == "/get-started"
    assert c.get("/").headers["location"] == "/get-started"
    assert c.get("/today").status_code == 200  # "Skip for now" works: Today is never blocked


def test_the_page_is_accessible_and_has_skip_and_the_fix_for_each_open_step(
    session: Session,
) -> None:
    c = web(session, InMemoryKeyStore())
    c.get(f"/?t={TOKEN}")
    html = c.get("/get-started").text
    assert check_page(html) == []
    assert 'href="/today">Skip for now' in html
    assert "hub models add" in html and "hub profile ready" in html and "hub nightly" in html
    assert html.count("Not done") == 6 and html.count(">Done<") == 1
    assert SECRET not in html


def test_once_steps_1_to_7_are_done_the_link_goes_to_today(session: Session, data: Path) -> None:
    store = InMemoryKeyStore()
    finish_setup(session, data, store)
    c = web(session, store)
    assert c.get(f"/?t={TOKEN}").headers["location"] == "/today"
    page = c.get("/get-started").text
    assert "Setup is complete" in page and check_page(page) == []


def test_without_a_key_store_or_data_the_app_goes_straight_to_today(session: Session) -> None:
    demo = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    c = TestClient(demo, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    assert c.get(f"/?t={TOKEN}").headers["location"] == "/today"
    bare = TestClient(create_app(TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}")
    assert bare.get(f"/?t={TOKEN}", follow_redirects=False).headers["location"] == "/today"


def test_the_profile_tab_links_to_it(session: Session) -> None:
    c = web(session, InMemoryKeyStore())
    c.get(f"/?t={TOKEN}")
    assert 'href="/get-started"' in c.get("/profile").text
