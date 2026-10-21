"""Reads busy time from a calendar file (`.ics`) the user exports or subscribes to.

Google Calendar and Outlook can both produce an iCalendar file or private feed, which needs no
sign-in and no app registration (live sync waits for CH-30). Only *when* is read: event titles,
descriptions, places, and attendees are never touched, and not kept. Rules:

- Repeating events are expanded.
- Events marked "free" (`TRANSP:TRANSPARENT`), cancelled events, and tentative-free all-day items
  such as holidays do not count as busy.
- All-day events that are marked busy block the whole local day.
- Times with no timezone are read in the user's timezone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo
from pathlib import Path

import icalendar
import recurring_ical_events

from careerhub.modules.plan.freebusy import Interval, merge


class IcsError(Exception):
    """The calendar file can't be read. The message never includes its contents."""


class IcsCalendar:
    """A `FreeBusySource` backed by iCalendar text."""

    def __init__(self, text: str | bytes, zone: tzinfo) -> None:
        """Parse `text`; naive times are interpreted in `zone`. Raises `IcsError` if unreadable."""
        try:
            decoded = text.decode("utf-8-sig") if isinstance(text, bytes) else text
            self._calendar = icalendar.Calendar.from_ical(decoded)
        except ValueError:
            raise IcsError("The calendar file is not valid iCalendar data.") from None
        self._zone = zone

    @classmethod
    def from_file(cls, path: Path, zone: tzinfo) -> IcsCalendar:
        """Read a calendar file from disk."""
        try:
            return cls(path.read_bytes(), zone)
        except OSError:
            raise IcsError(f"Can't read {path.name}.") from None

    def busy(self, start: datetime, end: datetime) -> list[Interval]:
        """Return merged busy intervals overlapping [start, end), clipped to it."""
        events = recurring_ical_events.of(self._calendar).between(start, end)
        found: list[Interval] = []
        for event in events:
            if str(event.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
                continue
            if str(event.get("STATUS", "")).upper() == "CANCELLED":
                continue
            span = self._span(event.start, event.end)
            low, high = max(span.start, start), min(span.end, end)
            if high > low:  # the library can return events that only touch the range
                found.append(Interval(low, high))
        return merge(found)

    def _span(self, begin: date | datetime, finish: date | datetime) -> Interval:
        """Convert an event's start and end (dates or datetimes) to a UTC interval."""
        return Interval(self._utc(begin), self._utc(finish))

    def _utc(self, value: date | datetime) -> datetime:
        if isinstance(value, datetime):
            return (
                value.astimezone(UTC)
                if value.tzinfo
                else value.replace(tzinfo=self._zone).astimezone(UTC)
            )
        return datetime.combine(value, time(0, 0), self._zone).astimezone(UTC)
