"""Suggested job sources from the conversation, saved as proposals (CH-161).

The model may suggest one company job board (Greenhouse, Lever or Ashby) when the person asks to
follow a company. Accepting the suggestion only saves it as a *proposed* source; the existing
source approval (`hub sources approve`) still decides, and nothing is fetched until then. The
address is built here from a checked board name, never taken from the model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Proposal, Source, utcnow
from careerhub.core.settings import load_settings
from careerhub.modules.jobs.sources import propose_source
from careerhub.modules.proposals import SOURCE_KIND, ProposalError, SourceChange


def propose_source_change(
    session: Session, change: SourceChange, now: datetime | None = None
) -> Proposal:
    """Save a pending proposal to add `change`'s board. Raises `ProposalError` if pointless."""
    now = now or utcnow()
    if session.scalars(select(Source).where(Source.url == change.url())).first() is not None:
        raise ProposalError("That job board is already in your sources.")
    body = change.model_dump_json()
    window = timedelta(days=load_settings().proposal_repeat_days)
    declined = session.scalars(
        select(Proposal).where(
            Proposal.kind == SOURCE_KIND, Proposal.body == body, Proposal.status == "rejected"
        )
    )
    if any(p.decided_at is not None and now - p.decided_at < window for p in declined):
        raise ProposalError("You declined that source recently, so it is not suggested again yet.")
    pending = session.scalars(
        select(Proposal).where(
            Proposal.kind == SOURCE_KIND, Proposal.body == body, Proposal.status == "pending"
        )
    ).first()
    if pending is not None:
        return pending
    proposal = Proposal(
        kind=SOURCE_KIND,
        title=change.title(),
        body=body,
        origin="conversation",
        target="source_list",
        diff=f"+ source: {change.company}\n+ address: {change.url()}\n"
        "Accepting only saves it as a proposed source. Nothing is fetched until you approve it.",
    )
    session.add(proposal)
    session.commit()
    return proposal


def accept_source(session: Session, proposal: Proposal, when: datetime) -> Proposal:
    """Save the board as a proposed source (the user approves it separately) and mark accepted."""
    try:
        change = SourceChange.model_validate_json(proposal.body or "{}")
    except ValueError:
        raise ProposalError("That suggestion is damaged, so it was not applied.") from None
    propose_source(session, change.kind, change.company, change.url(), {"board": change.token})
    proposal.status = "accepted"
    proposal.decided_at = when
    session.commit()
    return proposal
