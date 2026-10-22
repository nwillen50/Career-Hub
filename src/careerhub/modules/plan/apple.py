"""Busy time from Apple Calendar, through macOS EventKit (CH-83).

Any calendar in the Mac's Calendar app feeds the plan: iCloud, and Google or Outlook accounts
added in System Settings > Internet Accounts with Calendars turned on. This is the MVP provider.

Privacy by construction: the only things read from an event are its start, end, all-day flag,
availability, and status. Title, notes, location, URL, and attendees are never read (a test uses a
fake EventKit event that fails if anything else is touched). Free and cancelled events are skipped.

EventKit is imported lazily and only exists on macOS, so everywhere else the provider reports
itself unavailable. Reading events needs the user's permission (macOS asks once); a denial is
a clear message, not a crash. The `EventStore` seam lets tests use a fake store on Linux.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from careerhub.modules.plan.freebusy import Interval, merge

#: EventKit's raw values (EKEventAvailability and EKEventStatus).
AVAILABILITY_FREE = 1
STATUS_CANCELED = 3
ACCESS_TIMEOUT_SECONDS = 120.0
GRANT_HELP = (
    "Career Hub can't read your calendar. Allow it in System Settings > Privacy & Security > "
    "Calendars (add the terminal or app you run `hub` from), then try again."
)


class CalendarUnavailable(Exception):
    """Apple Calendar can't be used here. The message says why and what to do (never an event)."""


@dataclass(frozen=True)
class RawEvent:
    """The only facts read from an event."""

    start: datetime
    end: datetime
    all_day: bool = False
    availability: int = 0
    status: int = 0


class EventStore(Protocol):
    """What the provider needs from EventKit (or a fake of it in tests)."""

    def calendar_names(self) -> list[str]:
        """Return the names of the calendars available."""
        ...

    def events(self, start: datetime, end: datetime, names: Sequence[str]) -> Iterable[RawEvent]:
        """Return events overlapping [start, end) in the named calendars (all if none named)."""
        ...


class AppleCalendar:
    """A `FreeBusySource` backed by an `EventStore`."""

    def __init__(self, store: EventStore, calendars: Sequence[str] = ()) -> None:
        """Use `store`, limited to the calendars named in `calendars` (empty means all)."""
        self._store = store
        self._calendars = tuple(calendars)

    def busy(self, start: datetime, end: datetime) -> list[Interval]:
        """Return merged busy intervals overlapping [start, end), clipped to it."""
        found: list[Interval] = []
        for event in self._store.events(start, end, self._calendars):
            if event.availability == AVAILABILITY_FREE or event.status == STATUS_CANCELED:
                continue
            if event.start.tzinfo is None or event.end.tzinfo is None:
                continue  # never guess a timezone
            low, high = max(event.start.astimezone(UTC), start), min(event.end.astimezone(UTC), end)
            if high > low:
                found.append(Interval(low, high))
        return merge(found)


def available() -> bool:
    """Say whether EventKit can be used here (macOS with pyobjc installed)."""
    if sys.platform != "darwin":
        return False
    try:
        import EventKit  # noqa: F401
    except ImportError:
        return False
    return True


class EventKitStore:
    """The real store. Only works on macOS; build it with `open_event_kit_store`."""

    def __init__(self, ek: Any, store: Any, foundation: Any) -> None:
        """Wrap the EventKit module, an `EKEventStore`, and the Foundation module."""
        self._ek, self._store, self._foundation = ek, store, foundation

    def calendar_names(self) -> list[str]:
        """Return the calendars' display names (calendar names, not events)."""
        calendars = self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent)
        return [str(c.title()) for c in calendars]

    def events(self, start: datetime, end: datetime, names: Sequence[str]) -> list[RawEvent]:
        """Read start, end, all-day, availability, and status of events; nothing else."""
        calendars = list(self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent))
        if names:
            wanted = {n.strip().lower() for n in names}
            calendars = [c for c in calendars if str(c.title()).strip().lower() in wanted]
        if not calendars:
            return []
        date = self._foundation.NSDate.dateWithTimeIntervalSince1970_
        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            date(start.timestamp()), date(end.timestamp()), calendars
        )
        raw = self._store.eventsMatchingPredicate_(predicate) or []
        return [
            RawEvent(
                datetime.fromtimestamp(e.startDate().timeIntervalSince1970(), UTC),
                datetime.fromtimestamp(e.endDate().timeIntervalSince1970(), UTC),
                bool(e.isAllDay()),
                int(e.availability()),
                int(e.status()),
            )
            for e in raw
        ]


def open_event_kit_store() -> EventKitStore:
    """Ask macOS for read access to events and return the store.

    Raises `CalendarUnavailable` (with what to do) off macOS, without pyobjc, or on a denial.
    """
    if sys.platform != "darwin":
        raise CalendarUnavailable("Apple Calendar can only be read on a Mac.")
    try:
        import EventKit
        import Foundation
    except ImportError:
        raise CalendarUnavailable(
            "Apple Calendar needs the pyobjc-framework-EventKit package (installed on a Mac "
            "by `uv sync`)."
        ) from None
    store = EventKit.EKEventStore.alloc().init()
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    full_access = getattr(EventKit, "EKAuthorizationStatusFullAccess", 3)
    if status != full_access and status != getattr(EventKit, "EKAuthorizationStatusAuthorized", 3):
        done, result = threading.Event(), {"granted": False}

        def handler(granted: bool, error: Any) -> None:
            result["granted"] = bool(granted)
            done.set()

        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            store.requestFullAccessToEventsWithCompletion_(handler)
        else:
            store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, handler)
        if not done.wait(ACCESS_TIMEOUT_SECONDS) or not result["granted"]:
            raise CalendarUnavailable(GRANT_HELP)
    return EventKitStore(EventKit, store, Foundation)
