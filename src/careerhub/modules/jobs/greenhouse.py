"""Greenhouse: the public job-board feed of one company (`boards-api.greenhouse.io`).

Public data only, no key. `fetch_greenhouse` returns the board's complete current listing as
`NormalizedJob`s; `ingest_greenhouse` fetches and stores it, for an approved source only.

A failed fetch (network error, bad status, unreadable reply) raises `FetchError` and changes
nothing: in particular it never closes postings, because a partial or empty answer must not look
like "every job disappeared".
"""

from __future__ import annotations

import html
from html.parser import HTMLParser
from typing import Any

import httpx
from sqlalchemy.orm import Session

from careerhub.core.models import Source, utcnow
from careerhub.modules.jobs.sources import require_approved
from careerhub.modules.jobs.store import IngestReport, NormalizedJob, store_jobs

API_BASE = "https://boards-api.greenhouse.io/v1/boards"
KIND = "greenhouse"
TIMEOUT_SECONDS = 30.0


class FetchError(Exception):
    """A source couldn't be read. The message has no response content."""


class _TextOnly(HTMLParser):
    """Collects the visible text of an HTML fragment."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "li", "div", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")


def html_to_text(content: str) -> str:
    """Turn Greenhouse's HTML-escaped job description into plain text."""
    parser = _TextOnly()
    parser.feed(html.unescape(content))
    lines = (line.strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def board_token(source: Source) -> str:
    """Return the board name from the source's config (`{"board": "acme"}`)."""
    token = source.config.get("board", "").strip()
    if not token:
        raise FetchError(f"Source {source.id} has no 'board' in its config.")
    return token


def normalize(job: dict[str, Any], default_company: str) -> NormalizedJob:
    """Turn one Greenhouse job record into a `NormalizedJob`."""
    location = job.get("location") or {}
    content = job.get("content") or ""
    return NormalizedJob(
        company_name=job.get("company_name") or default_company,
        external_id=str(job["id"]),
        title=job["title"],
        url=job["absolute_url"],
        location=location.get("name") if isinstance(location, dict) else None,
        raw_text=html_to_text(content) if content else None,
        raw_payload=job,
    )


def fetch_greenhouse(source: Source, client: httpx.Client | None = None) -> list[NormalizedJob]:
    """Fetch the complete listing for an approved Greenhouse source."""
    require_approved(source)
    token = board_token(source)
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        reply = http.get(f"{API_BASE}/{token}/jobs", params={"content": "true"})
    except httpx.HTTPError as error:
        raise FetchError(f"Greenhouse request failed ({type(error).__name__}).") from None
    if reply.status_code != 200:
        raise FetchError(f"Greenhouse answered HTTP {reply.status_code} for board {token!r}.")
    try:
        records = reply.json()["jobs"]
        return [normalize(record, source.name) for record in records]
    except (ValueError, KeyError, TypeError):
        raise FetchError("Greenhouse's reply had an unexpected shape.") from None


def ingest_greenhouse(
    session: Session, source: Source, client: httpx.Client | None = None
) -> IngestReport:
    """Fetch and store one approved source, recording the fetch time and the source's health."""
    try:
        jobs = fetch_greenhouse(source, client)
    except FetchError:
        if source.status == "approved":
            source.health = "failing"
            session.commit()
        raise
    report = store_jobs(session, source, jobs)
    source.health = "ok"
    source.last_fetched_at = utcnow()
    session.commit()
    return report
