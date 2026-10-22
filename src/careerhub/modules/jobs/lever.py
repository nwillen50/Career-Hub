"""Lever: the public postings feed of one company (`api.lever.co/v0/postings/<company>`).

Public data only, no key. `fetch_lever` returns the company's complete current listing as
`NormalizedJob`s; `ingest_lever` fetches and stores it, for an approved source only. Failures
behave as in `greenhouse.py`: a failed fetch raises `FetchError` and never closes postings.

The source's config is `{"board": "<company>"}`, and optionally `{"region": "eu"}` for companies on
Lever's European site (`api.eu.lever.co`).
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from careerhub.core.models import Source
from careerhub.modules.jobs.greenhouse import board_token, html_to_text
from careerhub.modules.jobs.ingest import FetchError, ingest_source
from careerhub.modules.jobs.sources import require_approved
from careerhub.modules.jobs.store import IngestReport, NormalizedJob

KIND = "lever"
API_BASES = {"us": "https://api.lever.co/v0/postings", "eu": "https://api.eu.lever.co/v0/postings"}
TIMEOUT_SECONDS = 30.0
_REGION = re.compile(r"^(us|eu)$")


def _text(value: object) -> str:
    """Return `value` if it is a string, else an empty string."""
    return value if isinstance(value, str) else ""


def description(posting: dict[str, Any]) -> str:
    """Join Lever's description, its titled lists (requirements and so on), and closing text."""
    parts = [_text(posting.get("descriptionPlain"))]
    for section in posting.get("lists") or []:
        if isinstance(section, dict):
            parts.append(_text(section.get("text")))
            parts.append(html_to_text(_text(section.get("content"))))
    parts.append(_text(posting.get("additionalPlain")))
    return "\n".join(part.strip() for part in parts if part.strip())


def normalize(posting: dict[str, Any], company_name: str) -> NormalizedJob:
    """Turn one Lever posting into a `NormalizedJob` (Lever gives no company name; use ours)."""
    categories = posting.get("categories") or {}
    location = categories.get("location") if isinstance(categories, dict) else None
    text = description(posting)
    return NormalizedJob(
        company_name=company_name,
        external_id=str(posting["id"]),
        title=posting["text"],
        url=posting["hostedUrl"],
        location=location if isinstance(location, str) else None,
        raw_text=text or None,
        raw_payload=posting,
    )


def fetch_lever(source: Source, client: httpx.Client | None = None) -> list[NormalizedJob]:
    """Fetch the complete listing for an approved Lever source."""
    require_approved(source)
    token = board_token(source)
    region = str(source.config.get("region", "us")).lower()
    if not _REGION.match(region):
        raise FetchError(f"Source {source.id} has an invalid 'region' (use us or eu).")
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        reply = http.get(f"{API_BASES[region]}/{token}", params={"mode": "json"})
    except httpx.HTTPError as error:
        raise FetchError(f"Lever request failed ({type(error).__name__}).") from None
    if reply.status_code != 200:
        raise FetchError(f"Lever answered HTTP {reply.status_code} for {token!r}.")
    try:
        records = reply.json()
        if not isinstance(records, list):
            raise TypeError
        return [normalize(record, source.name) for record in records]
    except (ValueError, KeyError, TypeError):
        raise FetchError("Lever's reply had an unexpected shape.") from None


def ingest_lever(
    session: Session, source: Source, client: httpx.Client | None = None
) -> IngestReport:
    """Fetch and store one approved Lever source, recording its health."""
    return ingest_source(session, source, fetch_lever, "Lever", client)
