"""Proposed changes to preferences.yaml from the conversation, applied only on Accept (CH-159).

The model may suggest one small change (an excluded title word, company, or location, or turning a
work mode on or off). A suggestion becomes a pending `Proposal` with a readable before/after diff.
Nothing changes until the user presses Accept; Accept validates the result, saves a backup copy of
the old file next to it, and writes only `preferences.yaml` in the data folder. A change the user
declined is not proposed again for `proposal_repeat_days`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.files import write_private
from careerhub.core.models import Proposal, utcnow
from careerhub.core.settings import load_settings
from careerhub.profile.files import ProfileFileError, parse_yaml, preferences_path
from careerhub.profile.models import Preferences

KIND = "preference_change"
LIST_KEYS = ("excluded_title_words", "excluded_companies", "locations")
MODE_KEYS = ("remote_ok", "hybrid_ok", "onsite_ok")
MAX_VALUE_CHARS = 100


class ProposalError(Exception):
    """The proposal can't be made or applied; the message says why in plain words."""


class PreferenceChange(BaseModel):
    """One small change to the user's hard constraints."""

    model_config = ConfigDict(extra="forbid")

    key: Literal[
        "excluded_title_words",
        "excluded_companies",
        "locations",
        "remote_ok",
        "hybrid_ok",
        "onsite_ok",
    ]
    action: Literal["add", "remove", "set"]
    value: str | bool

    @model_validator(mode="after")
    def _consistent(self) -> PreferenceChange:
        if self.key in LIST_KEYS:
            if self.action == "set" or not isinstance(self.value, str):
                raise ValueError("a list setting is changed with add or remove and a text value")
            self.value = " ".join(self.value.split())
            if not self.value or len(self.value) > MAX_VALUE_CHARS:
                raise ValueError("the value must be 1 to 100 characters")
        elif self.action != "set" or not isinstance(self.value, bool):
            raise ValueError("a work mode is changed with set and true or false")
        return self

    def title(self) -> str:
        """Describe the change in a sentence."""
        if self.key in LIST_KEYS:
            word = "Add" if self.action == "add" else "Remove"
            where = "to" if self.action == "add" else "from"
            return f"{word} {self.value!r} {where} {self.key.replace('_', ' ')}"
        return f"Turn {self.key.removesuffix('_ok')} jobs {'on' if self.value else 'off'}"


class ConversationProposal(BaseModel):
    """What the model may attach to an answer (the schema the conversation prompt asks for)."""

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(min_length=1, max_length=6000)  # cut to 1,500 for display
    change: PreferenceChange | None = None


def _current() -> dict[str, Any]:
    path = preferences_path()
    if not path.exists():
        return {}
    return parse_yaml(path.read_text(encoding="utf-8"), path.name)


def _value(data: dict[str, Any], key: str) -> Any:
    """Return the current value of a hard-constraint key (a missing key means its default)."""
    default: Any = [] if key in LIST_KEYS else True
    hard = data.get("hard_constraints")
    if isinstance(hard, dict) and hard.get(key) is not None:
        return hard[key]
    return default


def apply_change(data: dict[str, Any], change: PreferenceChange) -> dict[str, Any]:
    """Return a copy of the preferences dict with `change` applied (no file is touched)."""
    new: dict[str, Any] = json.loads(json.dumps(data, default=str))
    hard = new.setdefault("hard_constraints", {})
    if not isinstance(hard, dict):
        raise ProposalError("hard_constraints in preferences.yaml is not a set of named values.")
    if change.key in LIST_KEYS:
        items = [str(x) for x in hard.get(change.key) or []]
        wanted = str(change.value).casefold()
        if change.action == "add" and wanted not in {x.casefold() for x in items}:
            items.append(str(change.value))
        if change.action == "remove":
            items = [x for x in items if x.casefold() != wanted]
        hard[change.key] = items
    else:
        hard[change.key] = change.value
    return new


def _show(value: Any) -> str:
    return yaml.safe_dump(value, default_flow_style=True).strip().removesuffix("...").strip()


def render_diff(old: dict[str, Any], new: dict[str, Any], key: str) -> str:
    """Show the one key that changes, before and after, in plain lines."""
    before = (old.get("hard_constraints") or {}).get(key, "[]" if key in LIST_KEYS else True)
    after = (new.get("hard_constraints") or {}).get(key)
    return f"hard_constraints.{key}\n- {_show(before)}\n+ {_show(after)}"


def propose(session: Session, change: PreferenceChange, now: datetime | None = None) -> Proposal:
    """Save a pending proposal for `change`, with its diff. Raises `ProposalError` if pointless."""
    now = now or utcnow()
    current = _current()
    new = apply_change(current, change)
    if _value(new, change.key) == _value(current, change.key):
        raise ProposalError("Your preferences already say that.")
    try:
        Preferences.model_validate(new)
    except ValidationError:
        raise ProposalError("That change would make preferences.yaml invalid.") from None
    window = timedelta(days=load_settings().proposal_repeat_days)
    body = change.model_dump_json()
    recent = session.scalars(
        select(Proposal).where(
            Proposal.kind == KIND, Proposal.body == body, Proposal.status == "rejected"
        )
    )
    if any(p.decided_at is not None and now - p.decided_at < window for p in recent):
        raise ProposalError("You declined that change recently, so it is not suggested again yet.")
    pending = session.scalars(
        select(Proposal).where(
            Proposal.kind == KIND, Proposal.body == body, Proposal.status == "pending"
        )
    ).first()
    if pending is not None:
        return pending
    proposal = Proposal(
        kind=KIND,
        title=change.title(),
        body=body,
        origin="conversation",
        target="preferences",
        diff=render_diff(current, new, change.key),
    )
    session.add(proposal)
    session.commit()
    return proposal


def pending_proposals(session: Session) -> list[Proposal]:
    """Return the conversation's preference proposals waiting for the user, oldest first."""
    return list(
        session.scalars(
            select(Proposal)
            .where(Proposal.kind == KIND, Proposal.status == "pending")
            .order_by(Proposal.id)
        )
    )


def _pending(session: Session, proposal_id: int) -> Proposal:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None or proposal.kind != KIND or proposal.status != "pending":
        raise ProposalError("That suggestion is no longer waiting for a decision.")
    return proposal


def _write_whole(path: Path, text: str) -> None:
    """Write `text` to `path` whole or not at all: a temporary file beside it, then a rename."""
    temporary = path.with_name(path.name + ".tmp")
    write_private(temporary, text)
    os.replace(temporary, path)


def _free_backup_name(path: Path, when: datetime) -> Path:
    """Return an unused `.before-<time>` name beside `path` (a counter breaks ties)."""
    base = f"{path.name}.before-{when:%Y%m%d-%H%M%S-%f}"
    candidate = path.with_name(base)
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f"{base}-{counter}")
    return candidate


def accept(session: Session, proposal_id: int, now: datetime | None = None) -> Proposal:
    """Apply a pending proposal to preferences.yaml (keeping a backup copy), mark it accepted.

    The change is applied only if the file still shows the same before/after the user saw.
    """
    proposal = _pending(session, proposal_id)
    change = PreferenceChange.model_validate_json(proposal.body or "{}")
    when = now or utcnow()
    try:
        current = _current()
        new = apply_change(current, change)
        Preferences.model_validate(new)
    except (ProfileFileError, ValidationError, ProposalError):
        raise ProposalError(
            "preferences.yaml has changed or has a problem, so the change was not applied."
        ) from None
    if render_diff(current, new, change.key) != proposal.diff:
        raise ProposalError(
            "Your preferences changed since this was suggested, so it was not applied. "
            "Ask again to get a fresh suggestion."
        )
    path = preferences_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _free_backup_name(path, when)
            with open(backup, "x", encoding="utf-8") as handle:
                os.chmod(backup, 0o600)
                handle.write(path.read_text(encoding="utf-8"))
        proposal.status = "accepted"
        proposal.decided_at = when
        session.flush()
        _write_whole(path, yaml.safe_dump(new, sort_keys=False, allow_unicode=True))
        session.commit()
    except OSError:
        session.rollback()
        raise ProposalError(
            "preferences.yaml could not be written, so nothing was changed."
        ) from None
    return proposal


def reject(session: Session, proposal_id: int, now: datetime | None = None) -> Proposal:
    """Mark a pending proposal rejected; the file is not touched."""
    proposal = _pending(session, proposal_id)
    proposal.status = "rejected"
    proposal.decided_at = now or utcnow()
    session.commit()
    return proposal
