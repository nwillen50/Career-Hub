"""Add a job by pasting its link (`hub add-job <url>`), CH-96.

Three paths, in this order:

1. **A Greenhouse, Lever, or Ashby posting address**: fetched through that system's public API.
2. **Any other site (LinkedIn, Handshake, ...)**: the page is *never* fetched or scraped, and no
   code here may request linkedin.com or joinhandshake.com. The user gives the company and title
   (or they are read from the link's own text); if the job is already known, or is on a company
   board we have an approved source for, that copy is used.
3. **Otherwise**: the user pastes the description; the job is stored with the original link.

Manual jobs belong to the source `manual`, whose listings never close a job (ADR-0011): the user
closes them, or a company feed that later lists the same job takes over through the usual dedupe.
Nothing here applies to a job or sends anything.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Job, Source
from careerhub.core.names import company_key
from careerhub.modules.jobs import ashby, greenhouse, lever
from careerhub.modules.jobs.adapters import ADAPTERS
from careerhub.modules.jobs.ingest import FetchError
from careerhub.modules.jobs.store import NormalizedJob, _squash, store_jobs

MANUAL_URL = "manual://added-by-you"
#: Sites that are never requested, whatever the user pastes.
NEVER_REQUEST = ("linkedin.com", "joinhandshake.com")
TIMEOUT_SECONDS = 30.0
_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SLUG = re.compile(r"/([a-z0-9-]+?)-at-([a-z0-9-]+?)-\d{4,}(?:/|\?|$)", re.IGNORECASE)


class ManualJobError(Exception):
    """The job couldn't be added; the message says what the user can do (no page content)."""


@dataclass(frozen=True)
class PostingRef:
    """A posting on a system with a public API."""

    kind: str
    board: str
    posting_id: str


@dataclass(frozen=True)
class AddResult:
    """How the job was added."""

    job: Job
    how: str


def is_never_requested(url: str) -> bool:
    """Say whether `url` is on a site this program must not request."""
    text = url.strip()
    try:
        parsed = urlparse(text if "//" in text else "//" + text)  # also catch "linkedin.com/jobs/1"
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return True  # a link that can't be read is never requested
    return any(host == site or host.endswith("." + site) for site in NEVER_REQUEST)


def classify(url: str) -> PostingRef | None:
    """Recognize a Greenhouse, Lever, or Ashby posting address (no request is made)."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.scheme not in ("http", "https") or is_never_requested(url):
        return None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and len(parts) >= 3:
        board, marker, posting_id = parts[0], parts[1], parts[2]
        ok = marker == "jobs"
        kind = "greenhouse"
    elif host == "jobs.lever.co" and len(parts) >= 2:
        board, posting_id, ok, kind = parts[0], parts[1], True, "lever"
    elif host == "jobs.ashbyhq.com" and len(parts) >= 2:
        board, posting_id, ok, kind = parts[0], parts[1], True, "ashby"
    else:
        return None
    if not (ok and _ID.match(board) and _ID.match(posting_id)):
        return None
    return PostingRef(kind, board, posting_id)


def guess_from_link(url: str) -> tuple[str, str] | None:
    """Read (title, company) from a link's own text, like `.../software-engineer-at-acme-12345`.

    Only the text of the address is read; the page is not requested. Returns None if it doesn't fit.
    """
    match = _SLUG.search(unquote(urlparse(url.strip()).path))
    if not match:
        return None
    return match.group(1).replace("-", " ").title(), match.group(2).replace("-", " ").title()


def _get(http: httpx.Client, url: str, params: dict[str, str] | None = None) -> httpx.Response:
    """Make one public API request (never to a site on the never-request list)."""
    if is_never_requested(url):
        raise ManualJobError("That site is never requested by Career Hub.")
    try:
        reply = http.get(url, params=params)
    except httpx.HTTPError as error:
        raise ManualJobError(f"The request failed ({type(error).__name__}).") from None
    if reply.status_code != 200:
        raise ManualJobError(f"The job board answered HTTP {reply.status_code}.")
    return reply


def fetch_posting(
    ref: PostingRef, company: str | None, client: httpx.Client | None
) -> NormalizedJob:
    """Fetch one posting through its system's public API."""
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    name = company or ref.board.replace("-", " ").title()
    try:
        if ref.kind == "greenhouse":
            record = _get(http, f"{greenhouse.API_BASE}/{ref.board}/jobs/{ref.posting_id}").json()
            return greenhouse.normalize(record, name)
        if ref.kind == "lever":
            base = lever.API_BASES["us"]
            return lever.normalize(_get(http, f"{base}/{ref.board}/{ref.posting_id}").json(), name)
        reply = _get(http, f"{ashby.API_BASE}/{ref.board}", {"includeCompensation": "true"})
        for record in reply.json()["jobs"]:
            if str(record.get("id")) == ref.posting_id and record.get("isListed", True):
                return ashby.normalize(record, name)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ManualJobError("The job board's reply had an unexpected shape.") from None
    raise ManualJobError("That posting was not found on the company's board (it may be closed).")


def manual_source(session: Session) -> Source:
    """Return the `manual` source, creating it (approved: adding a job is the user's own act)."""
    source = session.scalars(select(Source).where(Source.url == MANUAL_URL)).first()
    if source is None:
        source = Source(kind="manual", name="Added by you", url=MANUAL_URL, status="approved")
        session.add(source)
        session.commit()
    return source


def find_known(session: Session, company: str, title: str) -> Job | None:
    """Return a stored, open job at `company` with this title (ignoring case and punctuation)."""
    rows = session.scalars(
        select(Job)
        .join(Company, Job.company_id == Company.id)
        .where(Company.name_key == company_key(company), Job.status != "closed")
        .order_by(Job.id)
    )
    return next((job for job in rows if _squash(job.title) == _squash(title)), None)


def _from_company_board(
    session: Session, company: str, title: str, client: httpx.Client | None
) -> Job | None:
    """Refresh the company's own approved board sources, then look for the job again."""
    for source in session.scalars(select(Source).where(Source.status == "approved")):
        if source.kind in ADAPTERS and company_key(source.name) == company_key(company):
            try:
                ADAPTERS[source.kind](session, source, client)
            except FetchError:
                continue
            found = find_known(session, company, title)
            if found is not None:
                return found
    return None


def _store(session: Session, item: NormalizedJob) -> Job:
    store_jobs(session, manual_source(session), [item], close_missing=False)
    job = session.scalars(
        select(Job).where(
            Job.source_id == manual_source(session).id, Job.external_id == item.external_id
        )
    ).one()
    return job


def add_job(
    session: Session,
    url: str,
    company: str | None = None,
    title: str | None = None,
    text: str | None = None,
    client: httpx.Client | None = None,
) -> AddResult:
    """Add the job at `url` by the first path that works. Raises `ManualJobError` if none does."""
    ref = classify(url)
    if ref is not None:
        try:
            item = fetch_posting(ref, company, client)
            return AddResult(_store(session, item), f"fetched from {ref.kind}")
        except ManualJobError:
            if not (text and text.strip() and company and title):
                raise  # a closed posting can still be added from pasted text
    if company and title:
        known = find_known(session, company, title) or _from_company_board(
            session, company, title, client
        )
        if known is not None:
            return AddResult(known, "already in your list from the company's own board")
    if text and text.strip() and company and title:
        digest = hashlib.sha256(f"{url}|{company}|{title}".encode()).hexdigest()[:16]
        item = NormalizedJob(company, digest, title, url, None, text.strip(), {"added": "manual"})
        return AddResult(_store(session, item), "stored from the text you pasted")
    missing = [
        n for n, v in (("company", company), ("title", title), ("description text", text)) if not v
    ]
    raise ManualJobError(
        "Not found on a company board. To add it, give " + ", ".join(missing) + "."
        if missing
        else "The job could not be added."
    )
