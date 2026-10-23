"""What the Tracker page shows (CH-157): applications as a sortable, filterable table.

Query parameters are checked here, never trusted: an unknown view, sort column, or status falls
back to the default. Dates are shown and entered in the user's timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from careerhub.core.models import APPLICATION_STATUSES
from careerhub.core.settings import load_settings
from careerhub.modules.jobs.applications import (
    STATUS_LABELS,
    VIEWS,
    TrackerRow,
    export_csv,
    tracker_rows,
)

SORTS = ("company", "title", "status", "applied", "follow_up")


@dataclass(frozen=True)
class RowView:
    """One table row, ready for the template (dates as the user types them: YYYY-MM-DD)."""

    application_id: int
    job_id: int
    company: str
    title: str
    status: str
    applied: str
    follow_up: str
    next_step: str
    overdue: bool


@dataclass(frozen=True)
class TrackerQuery:
    """The checked filters and sort of one request."""

    view: str = "all"
    text: str = ""
    statuses: tuple[str, ...] = ()
    sort: str = "company"
    descending: bool = False

    def params(self, **change: str) -> str:
        """Return this query as a URL query string, with `change` applied."""
        from urllib.parse import urlencode

        values: dict[str, str] = {
            "view": self.view,
            "q": self.text,
            "sort": self.sort,
            "dir": "desc" if self.descending else "asc",
        }
        values.update(change)
        pairs = [(k, v) for k, v in values.items() if v and not (k == "view" and v == "all")]
        pairs += [("status", s) for s in self.statuses]
        return urlencode(pairs)


def clean_query(
    view: str | None,
    text: str | None,
    statuses: list[str],
    sort: str | None,
    direction: str | None,
) -> TrackerQuery:
    """Check the raw query parameters; anything unknown falls back to the default."""
    return TrackerQuery(
        view=view if view in dict(VIEWS) else "all",
        text=" ".join((text or "").split())[:100],
        statuses=tuple(s for s in statuses if s in APPLICATION_STATUSES),
        sort=sort if sort in SORTS else "company",
        descending=direction == "desc",
    )


def _day(moment: datetime | None, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).date().isoformat() if moment else ""


def to_rows(rows: list[TrackerRow], query: TrackerQuery, now: datetime) -> list[RowView]:
    """Turn data rows into table rows, sorted as asked."""
    zone = ZoneInfo(load_settings().timezone)
    views = [
        RowView(
            r.application_id,
            r.job_id,
            r.company,
            r.title,
            r.status,
            _day(r.applied_at, zone),
            _day(r.follow_up_due, zone),
            r.next_step or "",
            r.follow_up_due is not None and r.follow_up_due <= now,
        )
        for r in rows
    ]
    key = {
        "company": lambda v: (v.company.casefold(), v.title.casefold()),
        "title": lambda v: (v.title.casefold(), v.company.casefold()),
        "status": lambda v: (list(APPLICATION_STATUSES).index(v.status), v.company.casefold()),
        "applied": lambda v: (v.applied == "", v.applied),
        "follow_up": lambda v: (v.follow_up == "", v.follow_up),
    }[query.sort]
    return sorted(views, key=key, reverse=query.descending)


def gather_tracker(session: Session, query: TrackerQuery, now: datetime) -> list[RowView]:
    """List the applications matching `query`."""
    rows = tracker_rows(session, query.view, query.text, query.statuses, now)
    return to_rows(rows, query, now)


def csv_for(session: Session, query: TrackerQuery, now: datetime) -> str:
    """Return the matching applications as CSV text."""
    zone = ZoneInfo(load_settings().timezone)
    return export_csv(tracker_rows(session, query.view, query.text, query.statuses, now), zone)


def follow_up_from_text(text: str) -> datetime | None:
    """Read a date typed as YYYY-MM-DD (empty clears it) as the start of that day, user time.

    Raises `ValueError` for anything else.
    """
    text = text.strip()
    if not text:
        return None
    zone = ZoneInfo(load_settings().timezone)
    return datetime.combine(date.fromisoformat(text), time.min, tzinfo=zone).astimezone(UTC)


STATUS_CHOICES = [(s, STATUS_LABELS[s]) for s in APPLICATION_STATUSES]
VIEW_CHOICES = list(VIEWS)
