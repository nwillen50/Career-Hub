"""What every job-source adapter shares: the fetch error and the fetch-and-store routine.

An adapter provides `fetch(source, client) -> list[NormalizedJob]` (the complete current listing of
one approved source). `ingest_source` runs it, stores the result, and keeps the source's health:

- a failed fetch raises `FetchError`, changes nothing, and marks the source `failing`;
- an empty listing for a source that had open jobs is treated as a glitch: the jobs stay open and
  the source is marked `failing` (CH-78; CH-103 refines this).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Job, Source, utcnow
from careerhub.modules.jobs.store import IngestReport, NormalizedJob, store_jobs

Fetcher = Callable[[Source, httpx.Client | None], list[NormalizedJob]]


class FetchError(Exception):
    """A source couldn't be read. The message has no response content."""


def ingest_source(
    session: Session,
    source: Source,
    fetch: Fetcher,
    label: str,
    client: httpx.Client | None = None,
    partial: bool = False,
) -> IngestReport:
    """Fetch and store one approved source, recording the fetch time and the source's health.

    `partial` is for search sources that never list everything: their jobs are not closed for being
    missing, and an empty result is normal rather than a glitch.
    """
    try:
        jobs = fetch(source, client)
    except FetchError:
        if source.status == "approved":
            source.health = "failing"
            session.commit()
        raise
    if not partial and not jobs and _has_open_jobs(session, source):
        # An empty answer for a board that had jobs is more likely a glitch than a company that
        # closed every role; closing everything would blank the shortlist.
        source.health = "failing"
        session.commit()
        raise FetchError(f"{label} listed no jobs for a board that had open jobs; kept them.")
    report = store_jobs(session, source, jobs, partial=partial)
    source.health = "ok"
    source.last_fetched_at = utcnow()
    session.commit()
    return report


def _has_open_jobs(session: Session, source: Source) -> bool:
    """Say whether `source` has any job not yet closed."""
    return (
        session.scalars(
            select(Job.id).where(Job.source_id == source.id, Job.status != "closed").limit(1)
        ).first()
        is not None
    )
