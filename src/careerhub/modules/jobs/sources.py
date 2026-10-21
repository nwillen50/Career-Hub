"""Job sources and their approval. Nothing is fetched from a source the user hasn't approved.

A source starts `proposed` (whoever proposed it: the user, or later the agent). Only
`approve_source`, called by a user action such as `hub sources approve`, makes it `approved`.
Fetching code asks `require_approved` first, so a proposed, rejected, or disabled source can never
be used by accident (PRD: "No source is added without the user's approval, and a test proves it").
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Source, utcnow


class SourceNotApproved(Exception):
    """A source was used before the user approved it."""


class UnknownSource(KeyError):
    """No source has that id."""


def propose_source(
    session: Session, kind: str, name: str, url: str, config: dict[str, str] | None = None
) -> Source:
    """Add a source as `proposed`, or return the one already saved for this URL (unchanged)."""
    existing = session.scalars(select(Source).where(Source.url == url)).first()
    if existing is not None:
        return existing
    source = Source(kind=kind, name=name, url=url, config=config or {}, status="proposed")
    session.add(source)
    session.commit()
    return source


def _get(session: Session, source_id: int) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise UnknownSource(source_id)
    return source


def _decide(session: Session, source_id: int, status: str) -> Source:
    source = _get(session, source_id)
    source.status = status
    source.decided_at = utcnow()
    session.commit()
    return source


def approve_source(session: Session, source_id: int) -> Source:
    """Mark a source `approved`. Call this only from an action the user took."""
    return _decide(session, source_id, "approved")


def reject_source(session: Session, source_id: int) -> Source:
    """Mark a source `rejected`."""
    return _decide(session, source_id, "rejected")


def disable_source(session: Session, source_id: int) -> Source:
    """Stop using an approved source without forgetting it."""
    return _decide(session, source_id, "disabled")


def require_approved(source: Source) -> Source:
    """Return `source` if the user approved it; otherwise raise `SourceNotApproved`."""
    if source.status != "approved":
        raise SourceNotApproved(
            f"Source {source.id} ({source.name}) is {source.status}; approve it first."
        )
    return source


def approved_sources(session: Session, kind: str | None = None) -> list[Source]:
    """Return approved sources (optionally of one kind), oldest first."""
    query = select(Source).where(Source.status == "approved").order_by(Source.id)
    if kind is not None:
        query = query.where(Source.kind == kind)
    return list(session.scalars(query))


def list_sources(session: Session) -> list[Source]:
    """Return every source, oldest first."""
    return list(session.scalars(select(Source).order_by(Source.id)))
