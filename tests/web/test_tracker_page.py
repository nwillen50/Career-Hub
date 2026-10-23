"""The Tracker page: table, saved views, filters, inline edits, CSV (CH-157). Saved data."""

from __future__ import annotations

import csv
import io
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job  # noqa: F401  (saved-data helper)

from careerhub.core.migrate import migrate
from careerhub.core.models import Application, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.applications import set_follow_up, set_next_step, track
from careerhub.web.app import create_app
from careerhub.web.tracker_view import clean_query, follow_up_from_text

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}


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


def post(c: TestClient, path: str, body: str, htmx: bool = False) -> Any:
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/today").text)
    assert token
    headers = {**ORIGIN, **FORM, **({"hx-request": "true"} if htmx else {})}
    return c.post(path, headers=headers, content=f"csrf={token.group(1)}&{body}")


def tracked(session: Session) -> list[Application]:
    jobs = [
        add_job(session, n, 90 - n, company=name)
        for n, name in enumerate(("Acme", "Globex", "Initech"), 1)
    ]
    return [
        track(session, j.id, s)
        for j, s in zip(jobs, ("applied", "interested", "interviewing"), strict=True)
    ]


def test_empty_state_and_a_clean_accessible_page(session: Session) -> None:
    html = web(session).get("/tracker").text
    assert "No applications yet. Mark a job Applied on Today to start tracking." in html
    assert check_page(html) == []


def test_the_table_lists_applications_with_labelled_editable_cells(session: Session) -> None:
    tracked(session)
    html = web(session).get("/tracker").text
    assert "3 applications" in html and 'href="/jobs/1">Role 1</a>' in html
    assert 'aria-label="Status for Role 1 at Acme"' in html
    assert 'aria-label="Follow-up date for Role 1 at Acme"' in html
    assert '<option value="applied" selected>Applied</option>' in html
    assert check_page(html) == []
    assert html.index("Acme") < html.index("Globex") < html.index("Initech")  # sorted by company


def test_saved_views_status_chips_text_and_sorting(session: Session) -> None:
    apps = tracked(session)
    set_follow_up(session, apps[0].id, utcnow() - timedelta(days=1))
    c = web(session)
    due = c.get("/tracker?view=followups_due").text
    assert "Role 1" in due and "Role 2" not in due and 'aria-current="true">Follow-ups due' in due
    assert "Role 2" in c.get("/tracker?status=interested").text
    only = c.get("/tracker?status=interested&status=interviewing").text
    assert "Role 1" not in only and "Role 2" in only and "Role 3" in only
    assert (
        "Role 3" in c.get("/tracker?q=INITECH").text
        and "Role 1" not in c.get("/tracker?q=INITECH").text
    )
    assert "No applications match these filters." in c.get("/tracker?q=nothing").text
    descending = c.get("/tracker?sort=company&dir=desc").text
    assert descending.index("Initech") < descending.index("Acme")
    assert 'aria-sort="descending"' in descending


def test_bad_query_values_fall_back_instead_of_failing(session: Session) -> None:
    tracked(session)
    c = web(session)
    html = c.get("/tracker?view=<script>&sort=;drop&dir=sideways&status=hired&q=" + "x" * 500).text
    assert html and "<script>" not in html
    query = clean_query("nope", "  a   b ", ["applied", "bad"], "nope", "x")
    assert (query.view, query.text, query.statuses, query.sort) == (
        "all",
        "a b",
        ("applied",),
        "company",
    )


def test_an_htmx_edit_changes_status_follow_up_and_next_step_and_returns_the_row(
    session: Session,
) -> None:
    apps = tracked(session)
    c = web(session)
    body = "status=interviewing&follow_up=2030-01-15&next_step=email+the+recruiter"
    response = post(c, f"/applications/{apps[1].id}", body, htmx=True)
    assert response.status_code == 200 and response.text.lstrip().startswith("<tr")
    assert '<option value="interviewing" selected>' in response.text
    assert 'value="2030-01-15"' in response.text and "email the recruiter" in response.text
    session.expire_all()
    saved = session.get(Application, apps[1].id)
    assert saved is not None and saved.status == "interviewing" and saved.next_step


def test_a_plain_form_post_works_without_script_and_goes_back_to_the_tracker(
    session: Session,
) -> None:
    apps = tracked(session)
    c = web(session)
    response = post(c, f"/applications/{apps[0].id}", "status=offer")
    assert response.status_code == 303 and response.headers["location"] == "/tracker"
    session.expire_all()
    saved = session.get(Application, apps[0].id)
    assert saved is not None and saved.status == "offer"


def test_a_bad_date_or_status_is_a_message_on_the_row_and_changes_nothing(
    session: Session,
) -> None:
    apps = tracked(session)
    c = web(session)
    bad_date = post(c, f"/applications/{apps[0].id}", "follow_up=next+tuesday", htmx=True)
    assert 'role="alert"' in bad_date.text and "YYYY-MM-DD" in bad_date.text
    bad_status = post(c, f"/applications/{apps[0].id}", "status=hired", htmx=True)
    assert "Unknown status" in bad_status.text
    session.expire_all()
    saved = session.get(Application, apps[0].id)
    assert saved is not None and saved.status == "applied" and saved.follow_up_due is None
    assert "gone" in post(c, "/applications/9999", "status=offer", htmx=True).text


def test_an_empty_follow_up_clears_it(session: Session) -> None:
    apps = tracked(session)
    set_follow_up(session, apps[0].id, utcnow() + timedelta(days=3))
    post(web(session), f"/applications/{apps[0].id}", "follow_up=", htmx=True)
    session.expire_all()
    saved = session.get(Application, apps[0].id)
    assert saved is not None and saved.follow_up_due is None
    assert follow_up_from_text("") is None


def test_editing_needs_the_csrf_token_and_a_session(session: Session) -> None:
    apps = tracked(session)
    c = web(session)
    refused = c.post(
        f"/applications/{apps[0].id}", headers={**ORIGIN, **FORM}, content="status=offer"
    )
    assert refused.status_code == 403
    outsider = TestClient(
        create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind())),
        base_url=f"http://127.0.0.1:{PORT}",
    )
    assert outsider.get("/tracker").status_code == 403
    assert outsider.get("/tracker.csv").status_code == 403
    assert (
        outsider.post(
            f"/applications/{apps[0].id}", headers={**ORIGIN, **FORM}, content=""
        ).status_code
        == 403
    )


def test_hostile_text_is_escaped_in_rows_and_the_csv_is_spreadsheet_safe(session: Session) -> None:
    job = add_job(
        session, 1, 80, company='=HYPERLINK("http://evil")', title="<script>alert(1)</script>"
    )
    app = track(session, job.id, "applied")
    set_next_step(session, app.id, '"><script>alert(2)</script>')
    c = web(session)
    html = c.get("/tracker").text
    assert "<script>alert" not in html
    export = c.get("/tracker.csv")
    assert export.headers["content-type"].startswith("text/csv")
    assert 'filename="applications.csv"' in export.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(export.text)))
    assert rows[0][:3] == ["Company", "Role", "Status"] and rows[1][0].startswith("'=")


def test_the_csv_follows_the_same_filters(session: Session) -> None:
    tracked(session)
    rows = list(csv.reader(io.StringIO(web(session).get("/tracker.csv?status=interested").text)))
    assert [r[1] for r in rows[1:]] == ["Role 2"]


def test_a_follow_up_date_is_read_in_the_users_timezone() -> None:
    from datetime import UTC

    when = follow_up_from_text("2030-01-15")
    assert when is not None and when.tzinfo is not None
    assert when.astimezone(UTC).hour in (4, 5)  # midnight in New York is 04:00 or 05:00 UTC
    with pytest.raises(ValueError):
        follow_up_from_text("15/01/2030")


def test_a_bad_date_does_not_leave_the_status_half_changed(session: Session) -> None:
    """Review: the status was saved, then the bad date was reported as an error."""
    apps = tracked(session)
    c = web(session)
    bad = post(c, f"/applications/{apps[0].id}", "status=offer&follow_up=garbage", htmx=True)
    assert "YYYY-MM-DD" in bad.text
    session.expire_all()
    saved = session.get(Application, apps[0].id)
    assert saved is not None and saved.status == "applied"


def test_the_csv_dates_use_the_users_timezone_like_the_page(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: 2026-11-01 in Tokyo was written as 2026-10-31 (the UTC date)."""
    from careerhub.core.settings import Settings, save_settings

    save_settings(Settings(timezone="Asia/Tokyo"))
    apps = tracked(session)
    c = web(session)
    post(c, f"/applications/{apps[0].id}", "follow_up=2026-11-01", htmx=True)
    assert 'value="2026-11-01"' in c.get("/tracker").text
    rows = list(csv.reader(io.StringIO(c.get("/tracker.csv").text)))
    assert rows[1][4] == "2026-11-01"


def test_save_buttons_say_which_row_they_save(session: Session) -> None:
    tracked(session)
    assert 'aria-label="Save changes for Role 1 at Acme"' in web(session).get("/tracker").text
