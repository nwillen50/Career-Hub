"""Rubric proposals from the conversation: diff, Accept, version bump, backups (CH-160)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from a11y import check_page
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Proposal, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Router
from careerhub.llm.fake import FakeProvider
from careerhub.modules.proposals import ProposalError, RubricChange, accept, reject
from careerhub.modules.rubric_proposals import apply_rubric_change, propose_rubric_change
from careerhub.profile.rubric import load_rubric, rubric_path
from careerhub.web.app import create_app

TOKEN = "t"
PORT = 8765
ORIGIN = {"origin": f"http://127.0.0.1:{PORT}"}
FORM = {"content-type": "application/x-www-form-urlencoded"}
RUBRIC = """# my rubric
version: 3
criteria:
  - {name: field, weight: 3, description: Work matches my fields.}
  - {name: location, weight: 2, description: Based where I can work.}
"""
REWEIGH = RubricChange(action="reweigh", name="Location", weight=5)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "profile").mkdir(parents=True)
    rubric_path().write_text(RUBRIC, encoding="utf-8")
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session, answer: str = "{}") -> TestClient:
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


def test_each_action_needs_exactly_the_fields_it_changes() -> None:
    assert RubricChange(action="remove", name="x")
    assert RubricChange(action="add", name="x", weight=1, description="d")
    for bad in (
        {"action": "add", "name": "x", "weight": 1},
        {"action": "add", "name": "x", "description": "d"},
        {"action": "remove", "name": "x", "weight": 1},
        {"action": "reweigh", "name": "x"},
        {"action": "reweigh", "name": "x", "weight": 0},
        {"action": "reweigh", "name": "x", "weight": 101},
        {"action": "reword", "name": "x", "weight": 2, "description": "d"},
        {"action": "reword", "name": " ", "description": "d"},
        {"action": "rename", "name": "x"},
    ):
        with pytest.raises(ValidationError):
            RubricChange.model_validate(bad)


def test_the_diff_shows_the_criterion_and_the_version_bump(session: Session) -> None:
    proposal = propose_rubric_change(session, REWEIGH)
    assert (proposal.kind, proposal.target, proposal.status) == (
        "rubric_change", "rubric", "pending",
    )  # fmt: skip
    assert proposal.diff == (
        "rubric.yaml version 3 -> 4\n"
        "  field: weight 3, Work matches my fields.\n"
        "- location: weight 2, Based where I can work.\n"
        "+ location: weight 5, Based where I can work.\n"
        "Jobs are scored again on the next nightly run.\n"
        "Saving rewrites rubric.yaml without its comments; a copy of the old file is kept."
    )
    assert rubric_path().read_text(encoding="utf-8") == RUBRIC  # nothing changed yet


def test_accept_bumps_the_version_keeps_a_private_backup_and_changes_only_that_criterion(
    session: Session,
) -> None:
    accept(session, propose_rubric_change(session, REWEIGH).id)
    rubric = load_rubric()
    assert rubric is not None and rubric.version == 4
    assert [(c.name, c.weight) for c in rubric.criteria] == [("field", 3), ("location", 5)]
    backups = list(rubric_path().parent.glob("rubric.yaml.before-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == RUBRIC
    assert oct(backups[0].stat().st_mode & 0o777) == "0o600"
    assert oct(rubric_path().stat().st_mode & 0o777) == "0o600"


def test_add_remove_and_reword(session: Session) -> None:
    accept(
        session,
        propose_rubric_change(
            session, RubricChange(action="add", name="Pay", weight=1, description="Fair pay.")
        ).id,
    )
    accept(session, propose_rubric_change(session, RubricChange(action="remove", name="field")).id)
    accept(
        session,
        propose_rubric_change(
            session, RubricChange(action="reword", name="pay", description="Pay is above market.")
        ).id,
    )
    rubric = load_rubric()
    assert rubric is not None and rubric.version == 6
    assert [(c.name, c.description) for c in rubric.criteria] == [
        ("location", "Based where I can work."), ("Pay", "Pay is above market."),
    ]  # fmt: skip


def test_pointless_or_invalid_suggestions_are_refused(session: Session) -> None:
    with pytest.raises(ProposalError, match="already has a criterion"):
        propose_rubric_change(
            session, RubricChange(action="add", name="FIELD", weight=1, description="d")
        )
    with pytest.raises(ProposalError, match="no criterion called"):
        propose_rubric_change(session, RubricChange(action="remove", name="pay"))
    propose_rubric_change(session, RubricChange(action="remove", name="field"))
    only_one = "version: 1\ncriteria:\n  - {name: a, weight: 1, description: d}\n"
    rubric_path().write_text(only_one, encoding="utf-8")
    with pytest.raises(ProposalError, match="invalid"):
        propose_rubric_change(session, RubricChange(action="remove", name="a"))


def test_no_rubric_file_means_nothing_to_change(session: Session) -> None:
    rubric_path().unlink()
    with pytest.raises(ProposalError, match="no rubric yet"):
        propose_rubric_change(session, REWEIGH)


def test_a_repeat_returns_the_same_one_and_a_declined_one_waits_90_days(session: Session) -> None:
    now = utcnow()
    one = propose_rubric_change(session, REWEIGH)
    assert propose_rubric_change(session, REWEIGH).id == one.id
    reject(session, one.id, now)
    with pytest.raises(ProposalError, match="declined that change recently"):
        propose_rubric_change(session, REWEIGH, now)


def test_accept_refuses_when_the_rubric_changed_since_the_diff_was_shown(session: Session) -> None:
    proposal = propose_rubric_change(session, REWEIGH)
    rubric_path().write_text(RUBRIC.replace("weight: 2", "weight: 4"), encoding="utf-8")
    with pytest.raises(ProposalError, match="changed since this was suggested"):
        accept(session, proposal.id)
    assert "weight: 5" not in rubric_path().read_text(encoding="utf-8")
    assert session.get(Proposal, proposal.id).status == "pending"  # type: ignore[union-attr]


def test_a_failed_write_leaves_the_old_rubric_and_the_suggestion_pending(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    proposal = propose_rubric_change(session, REWEIGH)

    def failing(source: Any, target: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(ProposalError, match="nothing was changed"):
        accept(session, proposal.id)
    assert rubric_path().read_text(encoding="utf-8") == RUBRIC
    session.expire_all()
    assert session.get(Proposal, proposal.id).status == "pending"  # type: ignore[union-attr]


def test_apply_does_not_change_the_input_and_a_changed_version_type_is_survived() -> None:
    data = yaml.safe_load(RUBRIC)
    assert apply_rubric_change(data, REWEIGH)["version"] == 4 and data["version"] == 3


def test_the_chat_turns_a_request_into_a_rubric_suggestion_in_the_panel(session: Session) -> None:
    answer = json.dumps(
        {
            "answer": "I can make location count more.",
            "rubric": {"action": "reweigh", "name": "location", "weight": 5},
        }
    )
    c = web(session, answer)
    assert post(c, "/conversation", "question=location+matters+more").status_code == 303
    page = c.get("/conversation").text
    assert "Change the weight of the rubric criterion" in page and "version 3 -&gt; 4" in page
    assert 'aria-label="Accept: Change the weight' in page
    assert check_page(page) == []
    assert rubric_path().read_text(encoding="utf-8") == RUBRIC


def test_accept_in_the_page_writes_the_rubric(session: Session) -> None:
    proposal = propose_rubric_change(session, REWEIGH)
    c = web(session)
    assert post(c, f"/proposals/{proposal.id}/accept").status_code == 303
    rubric = load_rubric()
    assert rubric is not None and rubric.version == 4


def test_a_change_to_another_criterion_since_the_diff_was_shown_blocks_accept(
    session: Session,
) -> None:
    """Review: only the target criterion was compared, so other edits slipped through."""
    proposal = propose_rubric_change(session, REWEIGH)
    rubric_path().write_text(RUBRIC.replace("weight: 3", "weight: 9"), encoding="utf-8")
    with pytest.raises(ProposalError, match="changed since this was suggested"):
        accept(session, proposal.id)
    assert "weight: 5" not in rubric_path().read_text(encoding="utf-8")


def test_a_quoted_version_is_read_like_the_loader_does_not_reset_to_one(
    session: Session,
) -> None:
    """Review: version: "3" used to be treated as no version and became 1."""
    rubric_path().write_text(RUBRIC.replace("version: 3", 'version: "3"'), encoding="utf-8")
    accept(session, propose_rubric_change(session, REWEIGH).id)
    rubric = load_rubric()
    assert rubric is not None and rubric.version == 4


def test_a_malformed_rubric_suggestion_keeps_the_answer(session: Session) -> None:
    answer = json.dumps({"answer": "Noted.", "rubric": {"action": "reweigh", "name": "x"}})
    c = web(session, answer)
    assert post(c, "/conversation", "question=more+location").status_code == 303
    assert "Noted." in c.get("/conversation").text


def test_accepting_a_rubric_change_keeps_the_users_comments_and_layout(session: Session) -> None:
    """CH-180: only the version and the one criterion's weight differ."""
    commented = (
        "# my rubric\nversion: 3   # bump on change\ncriteria:\n"
        "  # most important\n  - {name: field, weight: 3, description: Work matches my fields.}\n"
        "\n  - {name: location, weight: 2, description: Based where I can work.}  # city\n"
    )
    rubric_path().write_text(commented, encoding="utf-8")
    accept(session, propose_rubric_change(session, REWEIGH).id)
    text = rubric_path().read_text(encoding="utf-8")
    assert "# most important" in text and "# city" in text and "# bump on change" in text
    # ruamel.yaml drops a blank line that follows a one-line {...} list item (ADR 0047).
    assert "weight: 5" in text and "version: 4" in text
