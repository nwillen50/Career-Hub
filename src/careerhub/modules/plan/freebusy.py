"""Busy and free time as plain intervals. No event titles, places, or attendees ever appear here.

Everything is timezone-aware UTC internally. A `FreeBusySource` answers "when is the user busy
between these two instants?"; `free_windows` turns that into the gaps inside the hours the user is
willing to work on the job search.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Protocol


@dataclass(frozen=True)
class Interval:
    """A span of time, `start` inclusive to `end` exclusive, both timezone-aware."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Reject naive datetimes and backwards spans: guessing a timezone causes silent bugs."""
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Interval times must have a timezone.")
        if self.end < self.start:
            raise ValueError("An interval can't end before it starts.")

    @property
    def minutes(self) -> int:
        """Length in whole minutes."""
        return int((self.end - self.start).total_seconds() // 60)


class FreeBusySource(Protocol):
    """Anything that can say when the user is busy (a calendar file now; Google, Outlook later)."""

    def busy(self, start: datetime, end: datetime) -> list[Interval]:
        """Return the busy intervals that overlap [start, end), clipped to it."""
        ...


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Combine overlapping or touching intervals into the fewest, in time order."""
    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    merged: list[Interval] = []
    for item in ordered:
        if merged and item.start <= merged[-1].end:
            if item.end > merged[-1].end:
                merged[-1] = Interval(merged[-1].start, item.end)
        else:
            merged.append(item)
    return merged


def free_windows(
    busy: Iterable[Interval], windows: Iterable[Interval], minimum_minutes: int = 30
) -> list[Interval]:
    """Return the parts of `windows` not covered by `busy`, keeping those >= `minimum_minutes`."""
    blocked = merge(busy)
    free: list[Interval] = []
    for window in sorted(windows, key=lambda i: i.start):
        cursor = window.start
        for item in blocked:
            if item.end <= cursor or item.start >= window.end:
                continue
            if item.start > cursor:
                free.append(Interval(cursor, item.start))
            cursor = max(cursor, item.end)
        if cursor < window.end:
            free.append(Interval(cursor, window.end))
    return [gap for gap in free if gap.minutes >= minimum_minutes]


def daily_windows(
    first_day: date,
    days: int,
    zone: tzinfo,
    start: time,
    end: time,
) -> list[Interval]:
    """Return one working window per day (in `zone`, converted to UTC) for `days` days.

    The hours are required: the plan's settings are the single source of truth for them.
    """
    windows = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        windows.append(
            Interval(
                datetime.combine(day, start, zone).astimezone(UTC),
                datetime.combine(day, end, zone).astimezone(UTC),
            )
        )
    return windows
