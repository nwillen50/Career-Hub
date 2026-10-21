from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from careerhub.modules.plan import IcsCalendar, Interval, daily_windows, free_windows, merge
from careerhub.modules.plan.ics import IcsError

NY = ZoneInfo("America/New_York")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_intervals_need_timezones_and_a_forward_span() -> None:
    with pytest.raises(ValueError):
        Interval(datetime(2026, 10, 1), at(1, 1))
    with pytest.raises(ValueError):
        Interval(at(1, 5), at(1, 4))
    assert Interval(at(1, 9), at(1, 10, 30)).minutes == 90


def test_merge_combines_overlapping_and_touching_intervals() -> None:
    merged = merge(
        [
            Interval(at(1, 11), at(1, 12)),
            Interval(at(1, 9), at(1, 10)),
            Interval(at(1, 10), at(1, 10, 30)),
            Interval(at(1, 9, 30), at(1, 10, 15)),
        ]
    )
    assert merged == [Interval(at(1, 9), at(1, 10, 30)), Interval(at(1, 11), at(1, 12))]


def test_free_windows_subtract_busy_time_and_drop_short_gaps() -> None:
    window = [Interval(at(1, 9), at(1, 17))]
    busy = [Interval(at(1, 10), at(1, 11)), Interval(at(1, 11, 15), at(1, 15))]
    gaps = free_windows(busy, window, minimum_minutes=30)
    assert gaps == [Interval(at(1, 9), at(1, 10)), Interval(at(1, 15), at(1, 17))]  # 15 min dropped


def test_busy_time_outside_the_window_changes_nothing() -> None:
    window = [Interval(at(1, 9), at(1, 12))]
    assert free_windows([Interval(at(1, 13), at(1, 14))], window) == window


def test_busy_time_covering_the_window_leaves_nothing() -> None:
    assert free_windows([Interval(at(1, 8), at(1, 13))], [Interval(at(1, 9), at(1, 12))]) == []


def test_daily_windows_follow_the_local_clock_across_daylight_saving() -> None:
    windows = daily_windows(date(2026, 11, 1), 2, NY, time(9), time(17))
    # US clocks go back on 2026-11-01 at 2am: 9am is 13:00 UTC before, 14:00 UTC after.
    assert windows[0].start == datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
    assert windows[1].start == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
    assert all(w.minutes == 480 for w in windows)


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1
DTSTAMP:20261001T000000Z
DTSTART:20261021T140000Z
DTEND:20261021T150000Z
SUMMARY:Secret therapy appointment
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTAMP:20261001T000000Z
DTSTART;TZID=America/New_York:20261022T100000
DTEND;TZID=America/New_York:20261022T110000
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Standup
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTAMP:20261001T000000Z
DTSTART:20261021T160000Z
DTEND:20261021T170000Z
TRANSP:TRANSPARENT
SUMMARY:Focus time (shows as free)
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTAMP:20261001T000000Z
DTSTART:20261021T180000Z
DTEND:20261021T190000Z
STATUS:CANCELLED
SUMMARY:Cancelled meeting
END:VEVENT
BEGIN:VEVENT
UID:5
DTSTAMP:20261001T000000Z
DTSTART;VALUE=DATE:20261023
DTEND;VALUE=DATE:20261024
SUMMARY:Conference (busy all day)
END:VEVENT
BEGIN:VEVENT
UID:6
DTSTAMP:20261001T000000Z
DTSTART;VALUE=DATE:20261024
DTEND;VALUE=DATE:20261025
TRANSP:TRANSPARENT
SUMMARY:Holiday
END:VEVENT
BEGIN:VEVENT
UID:7
DTSTAMP:20261001T000000Z
DTSTART:20261021T200000
DTEND:20261021T210000
SUMMARY:Floating time
END:VEVENT
END:VCALENDAR
"""


def test_busy_time_comes_from_events_and_ignores_free_cancelled_and_holiday_items() -> None:
    calendar = IcsCalendar(ICS, NY)
    busy = calendar.busy(at(21, 0), at(26, 0))
    spans = [(b.start, b.end) for b in busy]
    assert (at(21, 14), at(21, 15)) in spans  # plain UTC event
    assert (at(22, 14), at(22, 15)) in spans  # repeating 10am New York, day 1 (EDT = UTC-4)
    assert (at(24, 14), at(24, 15)) in spans  # day 3 (day 2 is inside the all-day block below)
    assert all(not (a == at(21, 16)) for a, _ in spans)  # TRANSPARENT skipped
    assert all(not (a == at(21, 18)) for a, _ in spans)  # CANCELLED skipped
    # the opaque all-day event blocks the whole New York day of Oct 23 (04:00Z to 04:00Z)
    assert any(a <= at(23, 4) and b >= at(24, 4) for a, b in spans)
    # the transparent all-day holiday on Oct 24 does not add a full-day block
    assert not any(a <= at(24, 4) and b >= at(25, 4) for a, b in spans)
    # a time with no zone is read in the user's zone: 20:00 New York = 00:00Z next day
    assert (at(22, 0), at(22, 1)) in spans


def test_events_are_clipped_to_the_question() -> None:
    busy = IcsCalendar(ICS, NY).busy(at(21, 14, 30), at(21, 14, 45))
    assert busy == [Interval(at(21, 14, 30), at(21, 14, 45))]


def test_no_event_text_is_ever_exposed() -> None:
    busy = IcsCalendar(ICS, NY).busy(at(21, 0), at(26, 0))
    assert "therapy" not in repr(busy) and "Standup" not in repr(busy)
    assert set(Interval.__dataclass_fields__) == {"start", "end"}


def test_bad_files_are_reported_without_their_contents(tmp_path: Path) -> None:
    with pytest.raises(IcsError) as raised:
        IcsCalendar("this is not a calendar SECRET", NY)
    assert "SECRET" not in str(raised.value)
    with pytest.raises(IcsError):
        IcsCalendar.from_file(tmp_path / "missing.ics", NY)


def test_a_file_on_disk_works_and_free_time_follows(tmp_path: Path) -> None:
    path = tmp_path / "cal.ics"
    path.write_text(ICS, encoding="utf-8")
    calendar = IcsCalendar.from_file(path, NY)
    day = daily_windows(date(2026, 10, 21), 1, NY, time(9), time(17))  # 13:00Z to 21:00Z
    gaps = free_windows(calendar.busy(day[0].start, day[0].end), day)
    assert gaps[0] == Interval(at(21, 13), at(21, 14))  # before the 10am-ET meeting
    assert gaps[1].start == at(21, 15)
    assert timedelta(0) < sum((g.end - g.start for g in gaps), timedelta()) < timedelta(hours=8)
