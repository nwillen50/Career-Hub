"""Saves normalized postings: one row per posting, no duplicates, closed when they disappear.

Every source adapter turns its feed into `NormalizedJob`s and hands a *complete* listing of one
source to `store_jobs`. Rules:

- A posting is identified by (source, external id). Seeing it again refreshes `last_seen_at` and
  its text, and reopens it if it had been closed.
- A posting the source no longer lists is marked `closed` (it is never deleted).
- A posting whose company, title, and location match one already stored from *another* source is
  linked to it with `duplicate_of_id`, so ranking can show each job once.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from careerhub.core.companies import get_or_create_company, suggest_merges
from careerhub.core.models import Company, Job, Source, utcnow
from careerhub.core.names import company_key


@dataclass(frozen=True)
class NormalizedJob:
    """A posting in the one shape every adapter produces."""

    company_name: str
    external_id: str
    title: str
    url: str
    location: str | None = None
    raw_text: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestReport:
    """What one `store_jobs` call did."""

    new: int = 0
    seen_again: int = 0
    reopened: int = 0
    closed: int = 0
    duplicates: int = 0


def _squash(text: str | None) -> str:
    """Lowercase and collapse spacing and punctuation so trivial differences don't matter."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def dedupe_key(
    company_name: str,
    title: str,
    location: str | None,
    aliases: Mapping[str, str] | None = None,
) -> str:
    """Return the hash that two postings of the same job share, whatever their source."""
    parts = "|".join((company_key(company_name, aliases), _squash(title), _squash(location)))
    return hashlib.sha256(parts.encode()).hexdigest()


def store_jobs(
    session: Session,
    source: Source,
    jobs: list[NormalizedJob],
    now: datetime | None = None,
    aliases: Mapping[str, str] | None = None,
) -> IngestReport:
    """Save a complete listing from `source`. Safe to repeat: a rerun adds no duplicates."""
    now = now or utcnow()
    existing = {
        job.external_id: job
        for job in session.scalars(select(Job).where(Job.source_id == source.id))
    }
    new = seen_again = reopened = duplicates = 0
    listed: set[str] = set()
    for item in jobs:
        listed.add(item.external_id)
        job = existing.get(item.external_id)
        if job is not None:
            reopened += job.status == "closed"
            seen_again += job.status != "closed"
            job.title, job.url, job.location = item.title, item.url, item.location
            job.raw_text, job.raw_payload = item.raw_text, item.raw_payload
            job.last_seen_at = now
            if job.status == "closed":
                job.status = "new"
            continue
        key = dedupe_key(item.company_name, item.title, item.location, aliases)
        original = session.scalars(
            select(Job)
            .where(Job.dedupe_key == key, Job.source_id != source.id, Job.duplicate_of_id.is_(None))
            .order_by(Job.first_seen_at, Job.id)
        ).first()
        session.add(
            Job(
                company=_company(session, item.company_name, aliases),
                source_id=source.id,
                external_id=item.external_id,
                title=item.title,
                url=item.url,
                location=item.location,
                raw_text=item.raw_text,
                raw_payload=item.raw_payload,
                first_seen_at=now,
                last_seen_at=now,
                dedupe_key=key,
                duplicate_of_id=original.id if original else None,
            )
        )
        new += 1
        duplicates += original is not None
    closed = 0
    for external_id, job in existing.items():
        if external_id not in listed and job.status != "closed":
            job.status = "closed"
            closed += 1
    session.commit()
    suggest_merges(session)
    return IngestReport(new, seen_again, reopened, closed, duplicates)


def _company(session: Session, name: str, aliases: Mapping[str, str] | None = None) -> Company:
    """Return the company called `name`, creating it if needed (matched by company key)."""
    company = get_or_create_company(session, name, aliases)
    if company is None:  # a posting with a blank company name
        company = get_or_create_company(session, "unknown company")
    assert company is not None
    return company


def only_visible_copies[Q](query: Q) -> Q:
    """Limit a query on `Job` to one copy of each job.

    A posting linked to an earlier one from another source (`duplicate_of_id`) is hidden, unless
    that original has closed: then the copy is the only open one and must stay visible.
    """
    original = aliased(Job)
    select_query: Any = query  # SQLAlchemy's Select is generic over its columns
    sibling = aliased(Job)
    older_open_copy = (
        select(sibling.id)
        .where(
            sibling.duplicate_of_id == Job.duplicate_of_id,
            sibling.id < Job.id,
            sibling.status != "closed",
        )
        .exists()
    )
    visible = select_query.outerjoin(original, original.id == Job.duplicate_of_id).where(
        or_(
            Job.duplicate_of_id.is_(None),
            and_(original.status == "closed", ~older_open_copy),  # one copy stands in
        )
    )
    return cast(Q, visible)
