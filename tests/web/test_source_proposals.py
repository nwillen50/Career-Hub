"""Source-list proposals from the conversation and the Declined list (CH-161). Saved data only."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Proposal, Source, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Router
from careerhub.llm.fake import FakeProvider
from careerhub.modules.jobs import approve_source
from careerhub.modules.proposals import (
    ProposalError,
    accept,
    declined_proposals,
    pending_proposals,
    reject,
    reopen,
)
from careerhub.modules.source_proposals import SourceChange, propose_source_change
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}
ACME = SourceChange(kind="greenhouse", company="Acme Robotics", token="acme")


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "profile").mkdir(parents=True)
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session, answer: str) -> TestClient:
    app = create_app(
        TOKEN,
        PORT,
        lambda: True,
        session_factory(session.get_bind()),
        open_router=lambda: (Router([FakeProvider("m", replies=[answer])]), ["m"]),
    )
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def post(c: TestClient, path: str, body: str = "") -> Any:
    token = re.search(r'name="csrf" value="([^"]+)"', c.get("/conversation").text)
    assert token
    return c.post(path, headers={**ORIGIN, **FORM}, content=f"csrf={token.group(1)}&{body}")


def test_the_board_name_is_checked_and_the_address_is_built_here() -> None:
    assert ACME.url() == "https://boards.greenhouse.io/acme"
    assert SourceChange(kind="lever", company="  Globex  ", token=" Globex ").url() == (
        "https://jobs.lever.co/globex"
    )
    for bad in (
        {"kind": "workday", "company": "A", "token": "acme"},
        {"kind": "lever", "company": "", "token": "acme"},
        {"kind": "lever", "company": "A", "token": "a"},
        {"kind": "lever", "company": "A", "token": "acme/../evil"},
        {"kind": "lever", "company": "A", "token": "https://evil.example"},
        {"kind": "lever", "company": "A", "token": "acme", "url": "https://evil.example"},
    ):
        with pytest.raises(ValidationError):
            SourceChange.model_validate(bad)


def test_a_suggestion_is_pending_and_adds_no_source_until_accepted(session: Session) -> None:
    proposal = propose_source_change(session, ACME)
    assert (proposal.kind, proposal.target, proposal.status) == (
        "source_proposal", "source_list", "pending",
    )  # fmt: skip
    assert "https://boards.greenhouse.io/acme" in (proposal.diff or "")
    assert session.query(Source).count() == 0


def test_accepting_saves_a_proposed_source_that_still_needs_approval(session: Session) -> None:
    accept(session, propose_source_change(session, ACME).id)
    source = session.query(Source).one()
    assert (source.status, source.kind, source.config) == (
        "proposed",
        "greenhouse",
        {"board": "acme"},
    )
    assert source.url == "https://boards.greenhouse.io/acme" and source.last_fetched_at is None
    approve_source(session, source.id)  # the existing flow still decides
    assert session.query(Source).one().status == "approved"


def test_a_board_already_in_the_list_is_not_suggested(session: Session) -> None:
    accept(session, propose_source_change(session, ACME).id)
    with pytest.raises(ProposalError, match="already in your sources"):
        propose_source_change(session, ACME)


def test_a_repeat_returns_the_same_pending_one(session: Session) -> None:
    one = propose_source_change(session, ACME)
    assert propose_source_change(session, ACME).id == one.id
    assert [p.id for p in pending_proposals(session)] == [one.id]


def test_declined_stays_listed_for_30_days_can_be_reopened_and_is_not_repeated_for_90(
    session: Session,
) -> None:
    now = utcnow()
    first = propose_source_change(session, ACME)
    reject(session, first.id, now)
    assert [p.id for p in declined_proposals(session, now + timedelta(days=29))] == [first.id]
    assert declined_proposals(session, now + timedelta(days=31)) == []
    with pytest.raises(ProposalError, match="declined that source recently"):
        propose_source_change(session, ACME, now + timedelta(days=89))
    with pytest.raises(ProposalError, match="too long ago"):
        reopen(session, first.id, now + timedelta(days=31))
    reopened = reopen(session, first.id, now + timedelta(days=2))
    assert reopened.status == "pending" and reopened.decided_at is None
    assert session.query(Source).count() == 0  # reopening applies nothing


def test_reopen_refuses_things_that_are_not_declined_or_already_waiting(session: Session) -> None:
    pending = propose_source_change(session, ACME)
    with pytest.raises(ProposalError, match="not on the Declined list"):
        reopen(session, pending.id)
    with pytest.raises(ProposalError, match="not on the Declined list"):
        reopen(session, 999)
    reject(session, pending.id)
    twin = Proposal(
        kind=pending.kind, title=pending.title, body=pending.body, status="pending",
        origin="conversation", target="source_list",
    )  # fmt: skip
    session.add(twin)
    session.commit()
    with pytest.raises(ProposalError, match="already waiting"):
        reopen(session, pending.id)


def test_the_chat_turns_a_request_into_a_source_suggestion(session: Session) -> None:
    answer = json.dumps(
        {
            "answer": "I can suggest it.",
            "change": None,
            "source": {"kind": "greenhouse", "company": "Acme Robotics", "token": "acme"},
        }
    )
    c = web(session, answer)
    assert post(c, "/conversation", "question=follow+Acme+Robotics").status_code == 303
    page = c.get("/conversation").text
    assert "Follow Acme Robotics jobs" in page and "Nothing is fetched until you approve it" in page
    assert 'aria-label="Accept: Follow Acme Robotics jobs' in page
    assert session.query(Source).count() == 0
    assert check_page(page) == []


def test_a_model_answer_with_a_bad_board_name_is_not_saved_as_a_suggestion(
    session: Session,
) -> None:
    answer = json.dumps(
        {"answer": "ok", "source": {"kind": "greenhouse", "company": "A", "token": "../x"}}
    )
    c = web(session, answer)
    post(c, "/conversation", "question=follow+it")
    assert pending_proposals(session) == []


def test_the_declined_list_in_the_panel_and_the_reopen_button(session: Session) -> None:
    proposal = propose_source_change(session, ACME)
    reject(session, proposal.id)
    c = web(session, "{}")
    page = c.get("/conversation").text
    assert "Declined" in page and 'aria-label="Reopen: Follow Acme Robotics jobs' in page
    assert check_page(page) == []
    assert post(c, f"/proposals/{proposal.id}/reopen").status_code == 303
    session.expire_all()
    assert session.get(Proposal, proposal.id).status == "pending"  # type: ignore[union-attr]
    assert "Declined" not in c.get("/conversation").text.split("Suggested changes")[0]
    assert "not on the Declined list" in post(c, f"/proposals/{proposal.id}/reopen").text


def test_no_declined_heading_when_nothing_was_declined(session: Session) -> None:
    assert 'id="decl-h"' not in web(session, "{}").get("/conversation").text


def test_one_bad_suggestion_keeps_the_answer_and_drops_only_the_suggestion(
    session: Session,
) -> None:
    """Review: a board name like acme_corp used to make the whole answer fail."""
    answer = json.dumps(
        {
            "answer": "Here is what I think.",
            "source": {"kind": "lever", "company": "A", "token": "acme_corp"},
        }
    )
    c = web(session, answer)
    assert post(c, "/conversation", "question=follow+it").status_code == 303
    assert "Here is what I think." in c.get("/conversation").text
    assert pending_proposals(session) == []


def test_a_board_saved_with_different_letter_case_is_not_added_twice(session: Session) -> None:
    session.add(
        Source(
            kind="greenhouse",
            name="Acme",
            url="https://boards.greenhouse.io/Acme",
            status="approved",
        )
    )
    session.commit()
    with pytest.raises(ProposalError, match="already in your sources"):
        propose_source_change(session, ACME)
    pending = Proposal(
        kind="source_proposal", title="t", body=ACME.model_dump_json(), status="pending",
        origin="conversation", target="source_list",
    )  # fmt: skip
    session.add(pending)
    session.commit()
    accept(session, pending.id)
    assert session.query(Source).count() == 1
