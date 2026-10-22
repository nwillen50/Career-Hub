"""Tests for the Apple Calendar provider against a fake store (CH-83). No Mac is needed."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.plan import apple
from careerhub.modules.plan.apple import (
    AppleCalendar,
    CalendarUnavailable,
    RawEvent,
    open_event_kit_store,
)
from careerhub.modules.plan.suggest import suggest_week

START = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
END = START + timedelta(days=7)


def at(hour: float, day: int = 21) -> datetime:
    return datetime(2026, 10, day, tzinfo=UTC) + timedelta(hours=hour)


class FakeStore:
    """An EventKit stand-in. `seen` records the calendars it was asked for."""

    def __init__(self, events: list[RawEvent], names: list[str] | None = None) -> None:
        self._events, self._names = events, names or ["Home"]
        self.seen: list[tuple[str, ...]] = []

    def calendar_names(self) -> list[str]:
        return self._names

    def events(self, start: datetime, end: datetime, names: Sequence[str]) -> Iterable[RawEvent]:
        self.seen.append(tuple(names))
        return self._events


def test_busy_events_become_merged_clipped_intervals() -> None:
    store = FakeStore(
        [
            RawEvent(at(10), at(11)),
            RawEvent(at(10.5), at(12)),  # overlaps: merged
            RawEvent(at(-2), at(1)),  # starts before the range: clipped
            RawEvent(at(24 * 6 + 20), at(24 * 8)),  # runs past the end: clipped
        ]
    )
    busy = AppleCalendar(store).busy(START, END)
    assert [(i.start, i.end) for i in busy] == [
        (START, at(1)),
        (at(10), at(12)),
        (at(24 * 6 + 20), END),
    ]


def test_free_and_cancelled_events_are_skipped() -> None:
    store = FakeStore(
        [
            RawEvent(at(9), at(10), availability=apple.AVAILABILITY_FREE),
            RawEvent(at(11), at(12), status=apple.STATUS_CANCELED),
            RawEvent(at(13), at(14), availability=0),  # busy
            RawEvent(at(15), at(16), availability=2),  # tentative still blocks time
        ]
    )
    assert [(i.start.hour, i.end.hour) for i in AppleCalendar(store).busy(START, END)] == [
        (13, 14),
        (15, 16),
    ]


def test_an_all_day_event_blocks_its_day_and_an_empty_or_naive_event_is_ignored() -> None:
    store = FakeStore(
        [
            RawEvent(at(0), at(24), all_day=True),
            RawEvent(at(5), at(5)),  # zero length
            RawEvent(datetime(2026, 10, 22, 9), datetime(2026, 10, 22, 10)),  # no timezone
        ]
    )
    busy = AppleCalendar(store).busy(START, END)
    assert [(i.start, i.end) for i in busy] == [(at(0), at(24))]


def test_the_calendars_named_in_settings_are_the_only_ones_asked_for() -> None:
    store = FakeStore([])
    AppleCalendar(store, ["Home", "Work"]).busy(START, END)
    AppleCalendar(store).busy(START, END)
    assert store.seen == [("Home", "Work"), ()]


def test_the_provider_has_no_way_to_receive_an_events_private_details() -> None:
    """`RawEvent` has no field for a title, notes, location, URL, or attendees."""
    fields = set(RawEvent.__dataclass_fields__)
    assert fields == {"start", "end", "all_day", "availability", "status"}


def test_the_real_store_reads_only_the_five_facts_from_an_eventkit_event() -> None:
    class Date:
        def __init__(self, value: float) -> None:
            self.value = value

        def timeIntervalSince1970(self) -> float:  # noqa: N802 (EventKit's spelling)
            return self.value

    class Event:
        """Allows exactly the five calls the provider is permitted; anything else fails."""

        def startDate(self) -> Date:  # noqa: N802
            return Date(at(9).timestamp())

        def endDate(self) -> Date:  # noqa: N802
            return Date(at(10).timestamp())

        def isAllDay(self) -> bool:  # noqa: N802
            return False

        def availability(self) -> int:
            return 0

        def status(self) -> int:
            return 0

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"read {name}")

    class Calendar:
        def __init__(self, title: str) -> None:
            self._title = title

        def title(self) -> str:
            return self._title

    class Foundation:
        class NSDate:
            @staticmethod
            def dateWithTimeIntervalSince1970_(seconds: float) -> float:  # noqa: N802
                return seconds

    class Kit:
        EKEntityTypeEvent = 0

    class Store:
        def calendarsForEntityType_(self, kind: int) -> list[Calendar]:  # noqa: N802
            return [Calendar("Home"), Calendar("Work")]

        def predicateForEventsWithStartDate_endDate_calendars_(  # noqa: N802
            self, start: float, end: float, calendars: list[Calendar]
        ) -> list[str]:
            return [c.title() for c in calendars]

        def eventsMatchingPredicate_(self, predicate: list[str]) -> list[Event]:  # noqa: N802
            return [Event()] * len(predicate)

    real = apple.EventKitStore(Kit, Store(), Foundation)
    assert real.calendar_names() == ["Home", "Work"]
    assert len(real.events(START, END, [])) == 2
    only_home = real.events(START, END, ["home"])
    assert len(only_home) == 1 and only_home[0].start == at(9)
    assert real.events(START, END, ["Nope"]) == []


def test_off_a_mac_the_provider_is_unavailable_with_a_clear_message() -> None:
    if apple.sys.platform != "darwin":
        assert apple.available() is False
        with pytest.raises(CalendarUnavailable, match="only be read on a Mac"):
            open_event_kit_store()


def test_without_pyobjc_the_message_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apple.sys, "platform", "darwin")
    monkeypatch.setitem(__import__("sys").modules, "EventKit", None)  # makes `import EventKit` fail
    with pytest.raises(CalendarUnavailable, match="pyobjc-framework-EventKit"):
        open_event_kit_store()


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_a_missing_calendar_is_reported_and_the_plan_still_works(session: Session) -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    plan = suggest_week(session, Settings(use_apple_calendar=True), 5.0, now)
    assert any("Apple Calendar" in problem or "Calendar" in problem for problem in plan.problems)
    quiet = suggest_week(session, Settings(), 5.0, now)
    assert quiet.problems == []


def test_the_settings_default_to_off_and_all_calendars() -> None:
    settings = Settings()
    assert settings.use_apple_calendar is False and settings.apple_calendars == ()
