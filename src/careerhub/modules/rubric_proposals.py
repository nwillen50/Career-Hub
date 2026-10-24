"""Suggested rubric changes from the conversation, applied only on Accept (CH-160).

The model may suggest adding, removing, reweighing or rewording one criterion. A suggestion becomes
a pending `Proposal` with a before/after diff. Accept validates the result, saves a backup of
`rubric.yaml` next to it, and writes it with `version` + 1. Scores are tied to the rubric version,
so the next nightly run re-scores the jobs. Nothing else is touched.
"""

from __future__ import annotations

import difflib
import os
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Proposal, utcnow
from careerhub.core.settings import load_settings
from careerhub.core.yamlfile import edit_yaml_text
from careerhub.modules.proposals import (
    RUBRIC_KIND,
    ProposalError,
    RubricChange,
    free_backup_name,
    write_whole,
)
from careerhub.profile.files import ProfileFileError, parse_yaml
from careerhub.profile.rubric import Rubric, rubric_path


def _current() -> dict[str, Any]:
    path = rubric_path()
    if not path.exists():
        raise ProposalError("There is no rubric yet, so there is nothing to change.")
    return parse_yaml(path.read_text(encoding="utf-8"), path.name)


def _criteria(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("criteria")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ProposalError("rubric.yaml has a problem, so it was not changed.")
    return [dict(i) for i in items]


def apply_rubric_change(data: dict[str, Any], change: RubricChange) -> dict[str, Any]:
    """Return a copy of the rubric dict with `change` applied and the version raised by one."""
    criteria = _criteria(data)
    wanted = change.name.casefold()
    index = next(
        (i for i, c in enumerate(criteria) if str(c.get("name", "")).casefold() == wanted), None
    )
    if change.action == "add":
        if index is not None:
            raise ProposalError(f"The rubric already has a criterion called {change.name!r}.")
        criteria.append(
            {"name": change.name, "weight": change.weight, "description": change.description}
        )
    else:
        if index is None:
            raise ProposalError(f"The rubric has no criterion called {change.name!r}.")
        if change.action == "remove":
            del criteria[index]
        elif change.action == "reweigh":
            criteria[index]["weight"] = change.weight
        else:
            criteria[index]["description"] = change.description
    try:
        version = Rubric.model_validate(data).version  # reads "3" as 3, like the loader does
    except ValidationError:
        raise ProposalError("rubric.yaml has a problem, so it was not changed.") from None
    return {**data, "version": version + 1, "criteria": criteria}


def _line(criterion: dict[str, Any]) -> str:
    weight = criterion.get("weight")
    shown = f"{weight:g}" if isinstance(weight, int | float) else str(weight)
    return f"{criterion.get('name')}: weight {shown}, {criterion.get('description')}"


def render_rubric_diff(old: dict[str, Any], new: dict[str, Any], name: str) -> str:
    """Show the whole rubric before and after: unchanged criteria plain, the change as -/+ lines.

    Showing every criterion means Accept can refuse if anything in the file differs from what the
    user was shown (not only the criterion being changed). `name` is kept for callers' symmetry.
    """
    before = [_line(c) for c in _criteria(old)]
    after = [_line(c) for c in _criteria(new)]
    body = [line for line in difflib.ndiff(before, after) if not line.startswith("? ")]
    return "\n".join(
        [
            f"rubric.yaml version {old.get('version')} -> {new.get('version')}",
            *body,
            "Jobs are scored again on the next nightly run.",
            "Saving rewrites rubric.yaml without its comments; a copy of the old file is kept.",
        ]
    )


def _check(new: dict[str, Any]) -> None:
    try:
        Rubric.model_validate(new)
    except ValidationError:
        raise ProposalError("That change would make rubric.yaml invalid.") from None


def propose_rubric_change(
    session: Session, change: RubricChange, now: datetime | None = None
) -> Proposal:
    """Save a pending proposal for `change`, with its diff. Raises `ProposalError` if pointless."""
    now = now or utcnow()
    try:
        current = _current()
        new = apply_rubric_change(current, change)
        _check(new)
        diff = render_rubric_diff(current, new, change.name)
    except ProfileFileError as problem:
        raise ProposalError(str(problem)) from None
    body = change.model_dump_json()
    window = timedelta(days=load_settings().proposal_repeat_days)
    declined = session.scalars(
        select(Proposal).where(
            Proposal.kind == RUBRIC_KIND, Proposal.body == body, Proposal.status == "rejected"
        )
    )
    if any(p.decided_at is not None and now - p.decided_at < window for p in declined):
        raise ProposalError("You declined that change recently, so it is not suggested again yet.")
    pending = session.scalars(
        select(Proposal).where(
            Proposal.kind == RUBRIC_KIND, Proposal.body == body, Proposal.status == "pending"
        )
    ).first()
    if pending is not None:
        return pending
    proposal = Proposal(
        kind=RUBRIC_KIND,
        title=change.title(),
        body=body,
        origin="conversation",
        target="rubric",
        diff=diff,
    )
    session.add(proposal)
    session.commit()
    return proposal


def accept_rubric(session: Session, proposal: Proposal, when: datetime) -> Proposal:
    """Write the changed rubric (version + 1) with a backup of the old file; mark accepted."""
    try:
        change = RubricChange.model_validate_json(proposal.body or "{}")
        current = _current()
        new = apply_rubric_change(current, change)
        _check(new)
        diff = render_rubric_diff(current, new, change.name)
    except (ProfileFileError, ProposalError, ValueError):
        raise ProposalError(
            "rubric.yaml has changed or has a problem, so the change was not applied."
        ) from None
    if diff != proposal.diff:
        raise ProposalError(
            "Your rubric changed since this was suggested, so it was not applied. "
            "Ask again to get a fresh suggestion."
        )
    path = rubric_path()
    original_text = path.read_text(encoding="utf-8")
    try:
        backup = free_backup_name(path, when)
        with open(backup, "x", encoding="utf-8") as handle:
            os.chmod(backup, 0o600)
            handle.write(path.read_text(encoding="utf-8"))
        proposal.status = "accepted"
        proposal.decided_at = when
        session.flush()
        write_whole(path, edit_yaml_text(original_text, new))
        session.commit()
    except OSError:
        session.rollback()
        raise ProposalError("rubric.yaml could not be written, so nothing was changed.") from None
    return proposal
