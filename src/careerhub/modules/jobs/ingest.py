"""What every job-source adapter shares: the fetch error and the fetch-and-store routine.

An adapter provides `fetch(source, client) -> list[NormalizedJob]` (the complete current listing of
one approved source). `ingest_source` runs it, stores the result, and keeps the source's health:

- a failed fetch raises `FetchError`, changes nothing, and marks the source `failing`;
- an empty listing for a source that had open jobs is either a glitch or a company that really
  closed everything (CH-103). Up to 3 open jobs are checked one by one where the system has a
  per-job address (`check_gone`): if all of them answer "not found" the closures are real and every
  job is closed; if any is still there, or a check fails, it is a glitch (jobs stay open, source
  `failing`). Systems with no per-job address rely on the backstop: after
  `empty_listing_backstop_nights` empty nights in a row the jobs are closed anyway and the nightly
  summary says so. Any non-empty listing resets the count. Only one empty listing per calendar day
  counts, and a job the system says is still listed never counts toward the backstop.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Job, Source, utcnow
from careerhub.core.settings import load_settings
from careerhub.modules.jobs.store import IngestReport, NormalizedJob, store_jobs

Fetcher = Callable[[Source, httpx.Client | None], list[NormalizedJob]]
#: Asks the source about one job: True = not found, False = still there, None = couldn't tell.
GoneCheck = Callable[[Source, Job, httpx.Client | None], bool | None]
#: How many open jobs are checked one by one when a board suddenly lists nothing.
SPOT_CHECKS = 3
STREAK_KEY = "empty_streak"
STREAK_DATE_KEY = "empty_streak_date"
_STREAK_KEYS = (STREAK_KEY, STREAK_DATE_KEY)


class FetchError(Exception):
    """A source couldn't be read. The message has no response content."""


def ingest_source(
    session: Session,
    source: Source,
    fetch: Fetcher,
    label: str,
    client: httpx.Client | None = None,
    partial: bool = False,
    check_gone: GoneCheck | None = None,
    backstop_nights: int | None = None,
    aliases: Mapping[str, str] | None = None,
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
    note = None
    if not partial and not jobs and _has_open_jobs(session, source):
        note = _settle_empty_listing(session, source, label, client, check_gone, backstop_nights)
    elif source.config.get(STREAK_KEY):
        source.config = {k: v for k, v in source.config.items() if k not in _STREAK_KEYS}
    report = store_jobs(session, source, jobs, partial=partial, aliases=aliases)
    source.health = "ok"
    source.last_fetched_at = utcnow()
    session.commit()
    return replace(report, note=note) if note else report


def _settle_empty_listing(
    session: Session,
    source: Source,
    label: str,
    client: httpx.Client | None,
    check_gone: GoneCheck | None,
    backstop_nights: int | None,
) -> str:
    """Decide whether an empty listing means everything closed; return the note, or raise.

    Raises `FetchError` (after marking the source failing) when it looks like a glitch.
    """
    open_jobs = list(
        session.scalars(
            select(Job).where(Job.source_id == source.id, Job.status != "closed").order_by(Job.id)
        )
    )
    count = len(open_jobs)
    answers = (
        [check_gone(source, job, client) for job in open_jobs[:SPOT_CHECKS]] if check_gone else []
    )
    if answers and all(answer is True for answer in answers):
        _reset_streak(source)
        return f"{source.name}: all {count} jobs closed"
    still_there = any(answer is False for answer in answers)
    streak = int(source.config.get(STREAK_KEY, "0"))
    today = utcnow().date().isoformat()
    # A job the system says is still listed means this is a glitch, never a closure; and only one
    # empty listing per calendar day counts, so repeated manual runs are not "three nights".
    if not still_there and source.config.get(STREAK_DATE_KEY) != today:
        streak += 1
    nights = backstop_nights or load_settings().empty_listing_backstop_nights
    if streak >= nights and not still_there:
        _reset_streak(source)
        return f"{source.name}: no jobs for {streak} nights in a row, so {count} jobs were closed"
    source.config = {**source.config, STREAK_KEY: str(streak), STREAK_DATE_KEY: today}
    source.health = "failing"
    session.commit()
    raise FetchError(f"{label} listed no jobs for a board that had open jobs; kept them.")


def _reset_streak(source: Source) -> None:
    source.config = {k: v for k, v in source.config.items() if k not in _STREAK_KEYS}


def _has_open_jobs(session: Session, source: Source) -> bool:
    """Say whether `source` has any job not yet closed."""
    return (
        session.scalars(
            select(Job.id).where(Job.source_id == source.id, Job.status != "closed").limit(1)
        ).first()
        is not None
    )
