"""Preference proposals from the conversation: diff, Accept, Reject, repeat rule (CH-159)."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_conversation import reply  # noqa: F401  (formats a model answer)
from test_today import add_job  # noqa: F401

from careerhub.core.migrate import migrate
from careerhub.core.models import Proposal, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Router
from careerhub.llm.fake import FakeProvider
from careerhub.modules.proposals import (
    PreferenceChange,
    ProposalError,
    accept,
    apply_change,
    pending_proposals,
    propose,
    reject,
    render_diff,
)
from careerhub.profile.files import load_preferences, preferences_path
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}
SALES = PreferenceChange(key="excluded_title_words", action="add", value="sales")
PREFS = """# my notes
hard_constraints:
  locations: [Boston, MA]
  excluded_title_words: [intern]
timeline:
  earliest_start: 2027-06-01
"""


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "profile").mkdir(parents=True)
    preferences_path().write_text(PREFS, encoding="utf-8")
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_the_change_schema_accepts_only_small_valid_changes() -> None:
    assert PreferenceChange(key="remote_ok", action="set", value=False)
    for bad in (
        {"key": "excluded_title_words", "action": "set", "value": "x"},
        {"key": "excluded_title_words", "action": "add", "value": ""},
        {"key": "excluded_title_words", "action": "add", "value": "x" * 101},
        {"key": "excluded_title_words", "action": "add", "value": True},
        {"key": "remote_ok", "action": "add", "value": True},
        {"key": "remote_ok", "action": "set", "value": "yes"},
        {"key": "min_salary_usd", "action": "set", "value": 1},
        {"key": "company_aliases", "action": "add", "value": "x"},
    ):
        with pytest.raises(ValidationError):
            PreferenceChange.model_validate(bad)


def test_the_diff_shows_the_key_before_and_after(session: Session) -> None:
    proposal = propose(session, SALES)
    assert proposal.status == "pending" and proposal.target == "preferences"
    assert proposal.origin == "conversation"
    assert proposal.diff == "hard_constraints.excluded_title_words\n- [intern]\n+ [intern, sales]"
    assert proposal.title == "Add 'sales' to excluded title words"
    assert preferences_path().read_text(encoding="utf-8") == PREFS  # nothing changed yet


def test_accept_writes_a_valid_file_keeps_a_backup_and_changes_only_that_key(
    session: Session,
) -> None:
    proposal = propose(session, SALES)
    accept(session, proposal.id)
    preferences = load_preferences()
    assert preferences is not None
    assert preferences.hard_constraints.excluded_title_words == ["intern", "sales"]
    assert preferences.hard_constraints.locations == ["Boston", "MA"]
    assert str(preferences.timeline.earliest_start) == "2027-06-01"
    backups = list(preferences_path().parent.glob("preferences.yaml.before-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == PREFS
    assert oct(preferences_path().stat().st_mode & 0o777) == "0o600"
    assert session.get(Proposal, proposal.id).status == "accepted"  # type: ignore[union-attr]
    with pytest.raises(ProposalError, match="no longer waiting"):
        accept(session, proposal.id)


def test_reject_leaves_the_file_and_the_same_change_is_not_suggested_again_for_90_days(
    session: Session,
) -> None:
    now = utcnow()
    first = propose(session, SALES)
    reject(session, first.id, now)
    assert preferences_path().read_text(encoding="utf-8") == PREFS
    with pytest.raises(ProposalError, match="declined that change recently"):
        propose(session, SALES, now + timedelta(days=89))
    again = propose(session, SALES, now + timedelta(days=91))
    assert again.id != first.id and again.status == "pending"


def test_pointless_or_invalid_suggestions_are_refused_and_a_repeat_returns_the_same_one(
    session: Session,
) -> None:
    with pytest.raises(ProposalError, match="already say that"):
        propose(session, PreferenceChange(key="excluded_title_words", action="add", value="Intern"))
    with pytest.raises(ProposalError, match="already say that"):
        propose(session, PreferenceChange(key="excluded_companies", action="remove", value="x"))
    one = propose(session, SALES)
    assert propose(session, SALES).id == one.id and len(pending_proposals(session)) == 1


def test_accept_refuses_when_the_file_changed_into_something_broken(session: Session) -> None:
    proposal = propose(session, SALES)
    preferences_path().write_text("hard_constraints: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(ProposalError, match="was not applied"):
        accept(session, proposal.id)
    assert session.get(Proposal, proposal.id).status == "pending"  # type: ignore[union-attr]


def test_work_modes_and_remove_and_a_missing_file(session: Session) -> None:
    turn_off = PreferenceChange(key="remote_ok", action="set", value=False)
    accept(session, propose(session, turn_off).id)
    prefs = load_preferences()
    assert prefs is not None and prefs.hard_constraints.remote_ok is False
    remove = PreferenceChange(key="locations", action="remove", value="boston")
    accept(session, propose(session, remove).id)
    prefs = load_preferences()
    assert prefs is not None and prefs.hard_constraints.locations == ["MA"]
    preferences_path().unlink()
    created = apply_change({}, SALES)
    assert created == {"hard_constraints": {"excluded_title_words": ["sales"]}}
    assert render_diff({}, created, "excluded_title_words").startswith("hard_constraints.")
    accept(session, propose(session, SALES).id)
    assert yaml.safe_load(preferences_path().read_text(encoding="utf-8"))["hard_constraints"]


def web(session: Session, answer: str) -> TestClient:
    fake = FakeProvider("m", replies=[answer])
    app = create_app(
        TOKEN,
        PORT,
        lambda: True,
        session_factory(session.get_bind()),
        open_router=lambda: (Router([fake]), ["m"]),
    )
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def post(c: TestClient, path: str, body: str = "") -> Any:
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/conversation").text)
    assert token
    return c.post(path, headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}&{body}")


def test_a_chat_request_becomes_a_pending_suggestion_with_a_diff_and_accept_reject_buttons(
    session: Session,
) -> None:
    change = {"key": "excluded_title_words", "action": "add", "value": "sales"}
    c = web(session, reply("I can add that. Please confirm below.", change))
    assert post(c, "/conversation", "question=I+don%27t+want+sales+roles").status_code == 303
    page = c.get("/conversation").text
    assert "Suggested changes waiting for you" in page and "+ [intern, sales]" in page
    assert 'action="/proposals/1/accept"' in page and 'action="/proposals/1/reject"' in page
    assert preferences_path().read_text(encoding="utf-8") == PREFS  # still nothing changed
    assert post(c, "/proposals/1/accept").status_code == 303
    prefs = load_preferences()
    assert prefs is not None and "sales" in prefs.hard_constraints.excluded_title_words
    assert "Suggested changes waiting" not in c.get("/conversation").text


def test_reject_in_the_page_and_a_model_suggestion_outside_the_allowed_keys(
    session: Session,
) -> None:
    allowed = {"key": "locations", "action": "add", "value": "Seattle"}
    c = web(session, reply("Added to your suggestions.", allowed))
    post(c, "/conversation", "question=add+seattle")
    assert post(c, "/proposals/1/reject").status_code == 303
    assert "Seattle" not in preferences_path().read_text(encoding="utf-8")
    forbidden = {"key": "min_salary_usd", "action": "set", "value": 1}
    c2 = web(session, reply("Sure.", forbidden))
    reply_page = post(c2, "/conversation", "question=raise+pay")
    assert reply_page.status_code == 303  # the answer is kept...
    assert "Sure." in c2.get("/conversation").text
    assert pending_proposals(session) == []  # ...but a key outside the allowed list is dropped


def test_deciding_needs_the_csrf_token_and_unknown_suggestions_are_friendly(
    session: Session,
) -> None:
    propose(session, SALES)
    c = web(session, reply("ok"))
    refused = c.post("/proposals/1/accept", headers={**ORIGIN, **FORM}, content="")
    assert refused.status_code == 403 and "sales" not in preferences_path().read_text("utf-8")
    page = post(c, "/proposals/99/accept")
    assert page.status_code == 200 and "no longer waiting" in page.text
    assert post(c, "/proposals/1/launch").status_code == 404
    assert json.loads(PreferenceChange(key="remote_ok", action="set", value=True).model_dump_json())


def test_a_failed_write_leaves_the_old_file_whole_and_the_suggestion_pending(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: a write that failed halfway left preferences.yaml empty."""
    import os

    proposal = propose(session, SALES)

    def failing_replace(source: Any, target: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(ProposalError, match="nothing was changed"):
        accept(session, proposal.id)
    assert preferences_path().read_text(encoding="utf-8") == PREFS
    session.expire_all()
    assert session.get(Proposal, proposal.id).status == "pending"  # type: ignore[union-attr]


def test_backups_are_private_and_two_accepts_in_one_second_keep_both(session: Session) -> None:
    now = utcnow()
    other = PreferenceChange(key="excluded_companies", action="add", value="Acme")
    first, second = propose(session, SALES), propose(session, other)
    accept(session, first.id, now)
    after_first = preferences_path().read_text(encoding="utf-8")
    accept(session, second.id, now)
    backups = sorted(preferences_path().parent.glob("preferences.yaml.before-*"))
    assert len(backups) == 2
    texts = {b.read_text(encoding="utf-8") for b in backups}
    assert PREFS in texts and after_first in texts  # the original is never overwritten
    assert all(oct(b.stat().st_mode & 0o777) == "0o600" for b in backups)


def test_accept_refuses_when_the_file_changed_since_the_diff_was_shown(session: Session) -> None:
    """Review: the file got a different result than the diff the user approved."""
    proposal = propose(session, SALES)
    preferences_path().write_text(PREFS.replace("[intern]", "[intern, zzz]"), encoding="utf-8")
    with pytest.raises(ProposalError, match="changed since this was suggested"):
        accept(session, proposal.id)
    assert "sales" not in preferences_path().read_text(encoding="utf-8")


def test_a_broken_preferences_file_does_not_waste_a_model_call_or_crash_the_chat(
    session: Session,
) -> None:
    preferences_path().write_text("a: [unclosed", encoding="utf-8")
    change = {"key": "excluded_title_words", "action": "add", "value": "sales"}
    c = web(session, reply("ok", change))
    page = post(c, "/conversation", "question=add+sales")
    assert page.status_code in (200, 303) and "Traceback" not in page.text


def test_accept_and_reject_buttons_name_their_suggestion(session: Session) -> None:
    propose(session, SALES)
    page = web(session, reply("ok")).get("/conversation").text
    assert 'aria-label="Accept: Add &#39;sales&#39; to excluded title words"' in page
