"""Reactions on job cards: interested, not interested with a reason, applied, undo (CH-140)."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job  # noqa: F401  (the saved-data helper Today's tests use)

from careerhub.core.migrate import migrate
from careerhub.core.models import Feedback, Job
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.reactions import (
    UNDO_SECONDS,
    ReactionError,
    current_reaction,
    react,
    reacted_job_ids,
    reaction_counts,
    undo,
)
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}


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


def csrf(c: TestClient) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', c.get("/today").text)
    assert match
    return match.group(1)


def post(c: TestClient, path: str, body: str, htmx: bool = False, token: str | None = None) -> Any:
    headers = {**ORIGIN, **FORM, **({"hx-request": "true"} if htmx else {})}
    return c.post(path, headers=headers, content=f"csrf={token or csrf(c)}&{body}")


def test_each_reaction_is_saved_and_the_newest_counts(session: Session) -> None:
    job = add_job(session, 1, 80)
    react(session, job.id, "interested")
    assert current_reaction(session, job.id) == "interested"
    react(session, job.id, "applied")
    assert current_reaction(session, job.id) == "applied"
    react(session, job.id, "not_interested", "pay")
    row = session.query(Feedback).order_by(Feedback.id.desc()).first()
    assert row is not None and (row.reaction, row.reason) == ("not_interested", "pay")


@pytest.mark.parametrize(
    ("reaction", "reason", "message"),
    [
        ("loved", None, "Unknown reaction"),
        ("interested", "pay", "Only 'not interested' takes a reason"),
        ("not_interested", "weather", "Unknown reason"),
    ],
)
def test_bad_reactions_are_refused(
    session: Session, reaction: str, reason: str | None, message: str
) -> None:
    job = add_job(session, 1, 80)
    with pytest.raises(ReactionError, match=message):
        react(session, job.id, reaction, reason)
    assert session.query(Feedback).count() == 0
    with pytest.raises(ReactionError, match="no longer exists"):
        react(session, 999, "interested")


def test_undo_works_for_ten_seconds_only(session: Session) -> None:
    job = add_job(session, 1, 80)
    react(session, job.id, "interested")
    assert undo(session, job.id) and session.query(Feedback).count() == 0
    row = react(session, job.id, "interested")
    assert not undo(session, job.id, now=row.created_at + timedelta(seconds=UNDO_SECONDS + 1))
    assert session.query(Feedback).count() == 1
    assert not undo(session, 12345)


def test_counts_use_the_newest_reaction_per_job(session: Session) -> None:
    a, b, c = (add_job(session, n, 80) for n in (1, 2, 3))
    react(session, a.id, "interested")
    react(session, a.id, "applied")
    react(session, b.id, "interested")
    react(session, c.id, "not_interested")
    assert reaction_counts(session) == {"interested": 1, "not_interested": 1, "applied": 1}
    assert reaction_counts(session, [a.id]) == {"interested": 0, "not_interested": 0, "applied": 1}
    assert reacted_job_ids(session) == {a.id, b.id, c.id}


def test_buttons_are_real_keyboard_buttons_in_a_form_that_works_without_script(
    session: Session,
) -> None:
    add_job(session, 1, 80)
    html = web(session).get("/today").text
    assert 'method="post" action="/jobs/1/react"' in html
    assert '<button type="submit" name="reaction" value="interested">Interested</button>' in html
    assert '<button type="submit" name="reaction" value="applied">Applied</button>' in html
    assert "<summary>Not interested</summary>" in html
    for reason in ("level", "location", "field", "company", "pay"):
        assert f"/jobs/1/react?reason={reason}" in html
    assert 'hx-post="/jobs/1/react"' in html


def test_a_plain_form_post_saves_and_goes_back_to_today(session: Session) -> None:
    job = add_job(session, 1, 80)
    c = web(session)
    response = post(c, "/jobs/1/react", "reaction=interested")
    assert response.status_code == 303 and response.headers["location"] == "/today"
    assert current_reaction(session, job.id) == "interested"
    assert "Role 1" not in c.get("/today").text  # reacted jobs are hidden from Today


def test_an_htmx_post_returns_the_card_replacement_with_undo(session: Session) -> None:
    job = add_job(session, 1, 80)
    c = web(session)
    response = post(c, "/jobs/1/react?reason=pay", "reaction=not_interested", htmx=True)
    assert response.status_code == 200
    text = response.text
    assert (
        "Marked <strong>not interested</strong> (Pay)" in text and "Undo (for 10 seconds)" in text
    )
    assert current_reaction(session, job.id) == "not_interested"
    undone = post(c, "/jobs/1/undo", "", htmx=True)
    assert "Reaction undone" in undone.text
    assert current_reaction(session, job.id) is None
    assert "Role 1" in c.get("/today").text  # back on Today


def test_a_reaction_needs_the_csrf_token_and_a_session(session: Session) -> None:
    job = add_job(session, 1, 80)
    c = web(session)
    assert post(c, "/jobs/1/react", "reaction=interested", token="wrong").status_code == 403
    outsider = TestClient(
        create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind())),
        base_url=f"http://127.0.0.1:{PORT}",
    )
    refused = outsider.post(
        "/jobs/1/react", headers={**ORIGIN, **FORM}, content="reaction=interested"
    )
    assert refused.status_code == 403 and session.query(Feedback).count() == 0
    assert job.id == 1


def test_an_unknown_job_or_reaction_gets_a_friendly_note_not_an_error(session: Session) -> None:
    add_job(session, 1, 80)
    c = web(session)
    gone = post(c, "/jobs/99/react", "reaction=interested", htmx=True)
    assert gone.status_code == 200 and "no longer exists" in gone.text
    bad = post(c, "/jobs/1/react", "reaction=nonsense", htmx=True)
    assert "Unknown reaction" in bad.text
    assert session.query(Feedback).count() == 0 and session.query(Job).count() == 1


def test_undo_takes_back_only_the_reaction_its_card_made(session: Session) -> None:
    """Review: with two tabs, undo on one card removed the other tab's newer reaction."""
    job = add_job(session, 1, 80)
    first = react(session, job.id, "interested")
    second = react(session, job.id, "applied")
    assert undo(session, job.id, first.id)
    assert current_reaction(session, job.id) == "applied"  # the newer one is untouched
    assert undo(session, job.id, second.id) and current_reaction(session, job.id) is None
    assert not undo(session, job.id, 999)


def test_undo_says_when_an_earlier_reaction_still_hides_the_job(session: Session) -> None:
    job = add_job(session, 1, 80)
    c = web(session)
    post(c, "/jobs/1/react", "reaction=interested", htmx=True)
    again = post(c, "/jobs/1/react", "reaction=interested", htmx=True)
    feedback_id = re.search(r'name="feedback_id" value="(\d+)"', again.text).group(1)  # type: ignore[union-attr]
    undone = post(c, "/jobs/1/undo", f"feedback_id={feedback_id}", htmx=True)
    assert "earlier reaction still keeps this job off Today" in undone.text
    assert job.id in reacted_job_ids(session)


def test_today_builds_cards_only_for_the_jobs_it_shows(session: Session) -> None:
    """Review: 2,000 queries to show five jobs when many top jobs had been reacted to."""
    from sqlalchemy import event

    for n in range(1, 41):
        add_job(session, n, 100 - n)
    for n in range(1, 36):
        react(session, n, "not_interested")
    counted: list[str] = []
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", lambda *a: counted.append(a[2]))
    html = web(session).get("/today").text
    assert "Top jobs (5)" in html and "Role 36" in html and "Role 35" not in html
    assert len(counted) < 120


def test_a_huge_job_id_is_refused_not_a_server_error(session: Session) -> None:
    """Review: a 23-digit job id used to raise OverflowError and answer 500."""
    add_job(session, 1, 80)
    c = web(session)
    huge = "9" * 23
    assert post(c, f"/jobs/{huge}/react", "reaction=interested").status_code == 422
    assert post(c, f"/jobs/{huge}/undo", "").status_code == 422
    assert post(c, "/jobs/0/react", "reaction=interested").status_code == 422
