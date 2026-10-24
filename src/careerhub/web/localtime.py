"""Show stored UTC times in the user's timezone (the `timezone` setting), never as UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def local_stamp(moment: datetime | None, zone: ZoneInfo) -> str | None:
    """Format `moment` like 'Fri Oct 23, 20:15' in `zone`; None if there is no moment.

    A naive datetime (SQLite may hand one back) is taken to be UTC, which is how Career Hub stores
    times.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return f"{moment.astimezone(zone):%a %b %d, %H:%M}"
