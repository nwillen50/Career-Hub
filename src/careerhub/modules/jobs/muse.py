"""The Muse: a broad, searchable source of early-career jobs (`www.themuse.com/api/public/jobs`).

Public data; a key is optional and not used. A search never lists every job, so this source is
**partial** (ADR-0011): a job that doesn't show up in tonight's search is not closed for that; it
closes after 14 days unseen. Every job keeps its Muse address (The Muse requires a link back) and
the shortlist shows "via The Muse".

The source's config narrows the search; all values are text:
`{"category": "Software Engineering", "level": "Entry Level", "pages": "3"}`
(`pages` is how many result pages to read, at most `MAX_PAGES`, to stay far below the rate limit of
about 500 requests an hour). The terms of use have not been verified by the build environment; see
the PR and the board.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from careerhub.core.models import Source
from careerhub.modules.jobs.greenhouse import html_to_text
from careerhub.modules.jobs.ingest import FetchError, ingest_source
from careerhub.modules.jobs.sources import require_approved
from careerhub.modules.jobs.store import IngestReport, NormalizedJob

KIND = "themuse"
VIA = "via The Muse"
API_URL = "https://www.themuse.com/api/public/jobs"
TIMEOUT_SECONDS = 30.0
DEFAULT_PAGES = 3
MAX_PAGES = 5
#: Config keys that become search filters, and the parameter The Muse calls each one.
FILTERS = {"category": "category", "level": "level", "location": "location"}


def normalize(record: dict[str, Any]) -> NormalizedJob:
    """Turn one Muse job into a `NormalizedJob` that keeps its Muse address."""
    locations = record.get("locations") or []
    names = [loc["name"] for loc in locations if isinstance(loc, dict) and loc.get("name")]
    contents = record.get("contents")
    return NormalizedJob(
        company_name=record["company"]["name"],
        external_id=str(record["id"]),
        title=record["name"],
        url=record["refs"]["landing_page"],
        location="; ".join(names) or None,
        raw_text=html_to_text(contents) if isinstance(contents, str) and contents else None,
        raw_payload=record,
    )


def _pages(source: Source) -> int:
    """Return how many pages to read, from the source's config, within 1 to `MAX_PAGES`."""
    try:
        wanted = int(source.config.get("pages", DEFAULT_PAGES))
    except ValueError:
        raise FetchError(f"Source {source.id} has a 'pages' value that is not a number.") from None
    return max(1, min(wanted, MAX_PAGES))


def fetch_muse(source: Source, client: httpx.Client | None = None) -> list[NormalizedJob]:
    """Search The Muse for an approved source and return what it found (a partial listing)."""
    require_approved(source)
    filters = {
        param: source.config[key] for key, param in FILTERS.items() if source.config.get(key)
    }
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    jobs: list[NormalizedJob] = []
    for page in range(_pages(source)):
        try:
            reply = http.get(API_URL, params={"page": page, **filters})
        except httpx.HTTPError as error:
            raise FetchError(f"The Muse request failed ({type(error).__name__}).") from None
        if reply.status_code != 200:
            raise FetchError(f"The Muse answered HTTP {reply.status_code}.")
        try:
            body = reply.json()
            jobs += [normalize(record) for record in body["results"]]
            if page + 1 >= int(body.get("page_count", 0)):
                break  # that was the last page
        except (ValueError, KeyError, TypeError, AttributeError):
            raise FetchError("The Muse's reply had an unexpected shape.") from None
        if reply.headers.get("X-RateLimit-Remaining", "1") in ("0", ""):
            break  # out of requests for now; keep what we have
    return jobs


def ingest_muse(
    session: Session, source: Source, client: httpx.Client | None = None
) -> IngestReport:
    """Search and store one approved Muse source (a partial listing: nothing closes for missing)."""
    return ingest_source(session, source, fetch_muse, "The Muse", client, partial=True)
