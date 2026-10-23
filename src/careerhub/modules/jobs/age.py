"""How old a posting is (CH-81): the source's posting date when it gives one, else first seen.

Every source names its date differently, so `posting_date` looks in the saved record for the field
each system uses. The age only ever shows as a footnote and gently lowers the ranking after
`stale_after_days`; a job is never hidden for its age (closed postings are handled by ingest).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from careerhub.core.models import Job

#: The date field each system offers, best first, in a job's saved record (`raw_payload`).
#: Greenhouse's public board gives `first_published` on some boards and `updated_at` (the last edit)
#: on all of them; Lever `createdAt` (milliseconds); Ashby `publishedAt`; The Muse
#: `publication_date`.
DATE_FIELDS = ("first_published", "publishedAt", "publication_date", "createdAt", "updated_at")


def _parse(value: object) -> date | None:
    """Read a date from an ISO text or a millisecond/second timestamp; None if it can't be read."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            seconds = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(seconds, UTC).date()
        if isinstance(value, str) and value.strip():
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
    except (ValueError, OverflowError, OSError):
        return None
    return None


def posting_date(job: Job) -> tuple[date, str]:
    """Return (the posting's date, where it came from: "source" or "first seen")."""
    payload = job.raw_payload if isinstance(job.raw_payload, dict) else {}
    for key in DATE_FIELDS:
        found = _parse(payload.get(key))
        if found is not None and found <= datetime.now(UTC).date():
            return found, "source"
    return job.first_seen_at.date(), "first seen"


def age_days(job: Job, now: datetime) -> int:
    """Return how many whole days old the posting is on `now` (never negative)."""
    return max(0, (now.date() - posting_date(job)[0]).days)
