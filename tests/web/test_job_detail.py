"""The Job detail page (CH-141). Read-only; saved data; every part has an empty state."""

from __future__ import annotations

from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job  # noqa: F401  (saved-data helper)

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Company, Job, Person, Source, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.reactions import react
from careerhub.web.app import create_app
from careerhub.web.job_detail import gather_job

TOKEN = "t"
PORT = 8765


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def rich_job(session: Session) -> Job:
    job = add_job(session, 1, 82, deadline="2030-01-15", salary=(100000, 120000))
    job.raw_text = "We build robots.\n<script>alert(1)</script>"
    job.structured_fields = {
        "fields": {
            "deadline": "2030-01-15",
            "salary_min_usd": 100000,
            "salary_max_usd": 120000,
            "summary": "Builds perception software for warehouse robots.",
            "role_focus": "applied ML, computer vision",
        }
    }
    assessment = session.query(Assessment).filter_by(job_id=job.id).one()
    assessment.filter_results = {"reasons": [], "uncertain": ["requires a security clearance"]}
    assessment.tailoring = {
        "notes": {
            "lead_with": ["Your vision project"],
            "gaps": ["No ROS"],
            "resume_suggestions": [],
        }
    }
    session.add(
        Person(
            full_name="Ada Sample", title="Engineer", company_id=job.company_id, how_known="mentor"
        )
    )
    session.commit()
    return job


def test_every_part_of_a_scored_job_is_shown(session: Session) -> None:
    job = rich_job(session)
    react(session, job.id, "interested")
    html = web(session).get(f"/jobs/{job.id}").text
    assert "<h1>Role 1</h1>" in html and "Fit score <strong>82</strong> out of 100" in html
    assert "$100,000 to $120,000" in html and "status: new" in html
    assert "Builds perception software" in html and "applied ML, computer vision" in html
    assert "Strong match. It fits." in html and "9/10" in html and "Right field" in html
    assert "requires a security clearance" in html
    assert "Ada Sample, Engineer" in html and "mentor" in html
    assert "Your vision project" in html and "No ROS" in html
    assert "Application deadline" in html and "Jan 15" in html
    assert "Posted today" in html and "Your reaction: <strong>interested</strong>" in html
    assert 'href="https://example.com/1"' in html and "Open the posting" in html
    assert '<button type="submit" name="reaction" value="applied">Applied</button>' in html


def test_the_posting_text_is_escaped_plain_text_and_collapsed(session: Session) -> None:
    job = rich_job(session)
    html = web(session).get(f"/jobs/{job.id}").text
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<details><summary>Show the posting text</summary><pre" in html
    assert "<details open" not in html


def test_empty_states_for_a_job_that_was_never_scored(session: Session) -> None:
    firm = Company(name="Nobody Inc", name_key="nobody")
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add_all([firm, source])
    session.flush()
    job = Job(
        company=firm,
        source_id=source.id,
        external_id="9",
        title="Bare Job",
        url="u",
        dedupe_key="k9",
        first_seen_at=utcnow(),
        last_seen_at=utcnow(),
    )
    session.add(job)
    session.commit()
    html = web(session).get(f"/jobs/{job.id}").text
    for phrase in (
        "Not scored yet",
        "No summary was read from the posting.",
        "This job has not been scored yet.",
        "Nothing to flag.",
        "You don't know anyone here yet.",
        "No tailoring notes yet",
        "No posting text was saved for this job.",
        "No safe link to the posting.",
    ):
        assert phrase in html, phrase


def test_hostile_text_and_unsafe_links_are_neutralised(session: Session) -> None:
    job = add_job(session, 1, 80, url="javascript:alert(1)", title="<img src=x onerror=alert(1)>")
    html = web(session).get(f"/jobs/{job.id}").text
    assert "<img src=x" not in html and "javascript:" not in html
    assert "No safe link to the posting." in html


def test_a_missing_job_gets_a_friendly_404(session: Session) -> None:
    response = web(session).get("/jobs/12345")
    assert response.status_code == 404 and "No such job" in response.text
    assert web(session).get("/jobs/abc").status_code in (404, 422)  # not a job id: no crash


def test_the_page_needs_a_session_and_passes_accessibility_checks(session: Session) -> None:
    job = rich_job(session)
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    outsider = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    assert outsider.get(f"/jobs/{job.id}").status_code == 403
    assert check_page(web(session).get(f"/jobs/{job.id}").text) == []


def test_reading_a_job_changes_nothing(session: Session) -> None:
    job = rich_job(session)
    before = (session.query(Job).count(), session.query(Assessment).count())
    web(session).get(f"/jobs/{job.id}")
    assert (session.query(Job).count(), session.query(Assessment).count()) == before
    assert gather_job(session, 999) is None


def test_today_cards_link_to_the_detail_page(session: Session) -> None:
    job = add_job(session, 1, 80)
    assert f'href="/jobs/{job.id}"' in web(session).get("/today").text
