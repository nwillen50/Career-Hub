"""Times on pages are shown in the user's timezone, never as UTC (bug CH-193)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from careerhub.web.localtime import local_stamp

NEW_YORK = ZoneInfo("America/New_York")


def test_a_utc_time_is_shown_in_the_users_zone() -> None:
    # 01:30 UTC on Oct 24 is 21:30 on Oct 23 in New York (before the November clock change).
    moment = datetime(2026, 10, 24, 1, 30, tzinfo=UTC)
    assert local_stamp(moment, NEW_YORK) == "Fri Oct 23, 21:30"


def test_a_naive_time_is_taken_as_utc_and_none_stays_none() -> None:
    assert local_stamp(datetime(2026, 10, 24, 1, 30), NEW_YORK) == "Fri Oct 23, 21:30"
    assert local_stamp(None, NEW_YORK) is None
