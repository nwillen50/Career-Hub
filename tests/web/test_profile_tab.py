"""The Profile tab (CH-195): what Career Hub knows, readiness, and the facts waiting for review."""

from __future__ import annotations

from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import ProfileExtraction
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.profile.readiness import set_scoring
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
PROFILE = """---
name: Jane Sample
education:
  - school: Example University
    degree: BS Physics
    graduation: 2027-05-01
skills: [Python, SQL]
interests: [robotics]
---
I like building things. <script>alert(1)</script>
"""
PREFS = """hard_constraints:
  locations: ['Boston, MA']
  min_salary_usd: 80000
  excluded_title_words: [sales]
weekly_time_budget_hours: 6
"""
RUBRIC = "version: 2\ncriteria:\n  - {name: field, weight: 3, description: Right field}\n"
FACTS = {"skills": ["Rust"], "gpa": 3.7}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def write_files(session: Session) -> Path:
    from careerhub.core.paths import data_dir

    root = data_dir()
    (root / "profile").mkdir(parents=True, exist_ok=True)
    (root / "profile" / "profile.md").write_text(PROFILE, encoding="utf-8")
    (root / "profile" / "preferences.yaml").write_text(PREFS, encoding="utf-8")
    (root / "profile" / "rubric.yaml").write_text(RUBRIC, encoding="utf-8")
    (root / "inputs" / "resume").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "resume" / "resume.txt").write_text("SECRET RESUME TEXT", encoding="utf-8")
    return root


def add_facts(session: Session, digit: str = "a") -> int:
    row = ProfileExtraction(
        kind="resume", file_sha256=digit * 64, prompt_version=1, status="pending", facts=FACTS
    )
    session.add(row)
    session.commit()
    return row.id


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    client.get(f"/?t={TOKEN}")
    return client


def test_the_page_shows_each_section_and_names_the_file_that_holds_it(session: Session) -> None:
    write_files(session)
    html = web(session).get("/profile").text
    for text in (
        "Jane Sample", "BS Physics, Example University (expected May 2027)", "Python, SQL",
        "robotics", "Boston, MA", "$80,000", "sales", "Right field", "version 2", "6 hours",
        "profile/profile.md", "profile/preferences.yaml", "profile/rubric.yaml",
    ):  # fmt: skip
        assert text in html, text
    assert "<script>alert" not in html and "&lt;script&gt;" in html  # profile text is escaped
    assert "SECRET RESUME TEXT" not in html  # document text is never shown
    assert check_page(html) == []


def test_readiness_says_what_is_missing_then_that_scoring_is_on(session: Session) -> None:
    html = web(session).get("/profile").text
    assert "Scoring is off" in html and "profile.md is missing" in html
    write_files(session)
    set_scoring(True, __import__("datetime").date(2026, 10, 24))
    assert "Scoring has been on since 2026-10-24" in web(session).get("/profile").text


def test_pending_facts_can_be_accepted_or_rejected_with_the_csrf_token(session: Session) -> None:
    write_files(session)
    first, second = add_facts(session, "a"), add_facts(session, "b")
    client = web(session)
    html = client.get("/profile").text
    assert "More skills: Rust" in html and "GPA: 3.7" in html and check_page(html) == []
    csrf = html.split('name="csrf" value="')[1].split('"')[0]
    refused = client.post(f"/profile/facts/{first}/accept", data={})
    assert refused.status_code == 403
    session.expire_all()
    assert session.get(ProfileExtraction, first).status == "pending"  # type: ignore[union-attr]
    ok = client.post(f"/profile/facts/{first}/accept", data={"csrf": csrf})
    assert ok.status_code == 303
    client.post(f"/profile/facts/{second}/reject", data={"csrf": csrf})
    session.expire_all()
    assert session.get(ProfileExtraction, first).status == "accepted"  # type: ignore[union-attr]
    assert session.get(ProfileExtraction, second).status == "rejected"  # type: ignore[union-attr]
    assert "No facts are waiting" in client.get("/profile").text
    again = client.post(f"/profile/facts/{first}/accept", data={"csrf": csrf})
    assert again.status_code == 303  # already decided elsewhere: no crash
    assert client.post(f"/profile/facts/{first}/bogus", data={"csrf": csrf}).status_code == 404


def test_a_broken_preferences_file_shows_the_designed_error_page(session: Session) -> None:
    root = write_files(session)
    (root / "profile" / "preferences.yaml").write_text("locatoins: [x]\n", encoding="utf-8")
    page = web(session).get("/profile")
    assert page.status_code == 503 and "settings file has a problem" in page.text


def test_review_findings_facts_that_no_longer_fit_the_schema_do_not_crash_the_tab(
    session: Session,
) -> None:
    write_files(session)
    row = ProfileExtraction(
        kind="resume", file_sha256="c" * 64, prompt_version=1, status="pending",
        facts={"gpa": "not a number", "skills": 5},
    )  # fmt: skip
    session.add(row)
    session.commit()
    page = web(session).get("/profile")
    assert page.status_code == 200 and "cannot be shown" in page.text
    assert f"/profile/facts/{row.id}/reject" in page.text
