"""Ashby: the public job board of one company (`api.ashbyhq.com/posting-api/job-board/<board>`).

Public data only, no key. Same shape and rules as `greenhouse.py` and `lever.py`. The request asks
for compensation (`includeCompensation=true`); when a posting states a salary, a "Compensation"
line is added to the text so extraction and the salary filter can see it. Postings Ashby marks
unlisted (`isListed: false`) are left out. Ashby has no per-job address, so closures rely on the
nightly rules in `ingest.py`.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from careerhub.core.models import Source
from careerhub.modules.jobs.greenhouse import board_token, html_to_text
from careerhub.modules.jobs.ingest import FetchError, ingest_source
from careerhub.modules.jobs.sources import require_approved
from careerhub.modules.jobs.store import IngestReport, NormalizedJob

KIND = "ashby"
API_BASE = "https://api.ashbyhq.com/posting-api/job-board"
TIMEOUT_SECONDS = 30.0


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def compensation_line(posting: dict[str, Any]) -> str:
    """Describe a stated salary range, e.g. "Compensation: USD 120000 to 150000 per year"."""
    compensation = posting.get("compensation")
    components = compensation.get("summaryComponents") if isinstance(compensation, dict) else None
    for part in components or []:
        if not isinstance(part, dict) or part.get("compensationType") != "Salary":
            continue
        low, high = part.get("minValue"), part.get("maxValue")
        if isinstance(low, int | float) and isinstance(high, int | float):
            interval = _text(part.get("interval")).replace("1 ", "per ").lower()
            currency = _text(part.get("currencyCode")) or "USD"
            return f"Compensation: {currency} {low:g} to {high:g} {interval}".strip()
    return ""


def normalize(posting: dict[str, Any], company_name: str) -> NormalizedJob:
    """Turn one Ashby posting into a `NormalizedJob`."""
    body = _text(posting.get("descriptionPlain")) or html_to_text(
        _text(posting.get("descriptionHtml"))
    )
    lines = [body.strip(), compensation_line(posting)]
    if posting.get("isRemote") is True:
        lines.append("Workplace: remote")
    text = "\n".join(line for line in lines if line)
    location = posting.get("location")
    return NormalizedJob(
        company_name=company_name,
        external_id=str(posting["id"]),
        title=posting["title"],
        url=posting["jobUrl"],
        location=location if isinstance(location, str) else None,
        raw_text=text or None,
        raw_payload=posting,
    )


def fetch_ashby(source: Source, client: httpx.Client | None = None) -> list[NormalizedJob]:
    """Fetch the complete listing for an approved Ashby source."""
    require_approved(source)
    token = board_token(source)
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        reply = http.get(f"{API_BASE}/{token}", params={"includeCompensation": "true"})
    except httpx.HTTPError as error:
        raise FetchError(f"Ashby request failed ({type(error).__name__}).") from None
    if reply.status_code != 200:
        raise FetchError(f"Ashby answered HTTP {reply.status_code} for board {token!r}.")
    try:
        records = reply.json()["jobs"]
        return [
            normalize(record, source.name) for record in records if record.get("isListed", True)
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise FetchError("Ashby's reply had an unexpected shape.") from None


def ingest_ashby(
    session: Session, source: Source, client: httpx.Client | None = None
) -> IngestReport:
    """Fetch and store one approved Ashby source, recording its health."""
    return ingest_source(session, source, fetch_ashby, "Ashby", client)
