"""Builds this week's suggested plan from settings, calendar files, and the shortlist."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from careerhub.core.settings import Settings
from careerhub.modules.plan.freebusy import Interval, daily_windows, free_windows
from careerhub.modules.plan.ics import IcsCalendar
from careerhub.modules.plan.weekly import WeekPlan, build_tasks, plan_week

#: Hours of the day the plan may use, in the user's timezone.
DAY_START = time(9, 0)
DAY_END = time(21, 0)
DAYS = 7


def suggest_week(
    session: Session, settings: Settings, budget_hours: float, now: datetime
) -> WeekPlan:
    """Plan the next seven days. Only free time from now on is used; nothing is sent or saved."""
    zone = ZoneInfo(settings.timezone)
    today = now.astimezone(zone).date()
    windows = [
        Interval(max(window.start, now), window.end)
        for window in daily_windows(today, DAYS, zone, DAY_START, DAY_END)
        if window.end > now
    ]
    horizon_start, horizon_end = windows[0].start, windows[-1].end
    busy: list[Interval] = []
    for name in settings.calendar_files:
        calendar = IcsCalendar.from_file(Path(name).expanduser(), zone)
        busy += calendar.busy(horizon_start, horizon_end)
    free = free_windows(busy, windows)
    tasks = build_tasks(session, zone)
    return plan_week(tasks, free, int(budget_hours * 60), zone)
