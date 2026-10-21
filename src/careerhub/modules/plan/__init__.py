"""The Plan module: free time and the weekly time budget become suggested work blocks."""

from careerhub.modules.plan.freebusy import (
    FreeBusySource,
    Interval,
    daily_windows,
    free_windows,
    merge,
)
from careerhub.modules.plan.ics import IcsCalendar

__all__ = [
    "FreeBusySource",
    "IcsCalendar",
    "Interval",
    "daily_windows",
    "free_windows",
    "merge",
]
