"""Builds this week's suggested plan from settings, calendar files, and the shortlist."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from careerhub.core.settings import Settings
from careerhub.modules.plan.apple import AppleCalendar, CalendarUnavailable, open_event_kit_store
from careerhub.modules.plan.freebusy import Interval, daily_windows, free_windows
from careerhub.modules.plan.ics import IcsCalendar
from careerhub.modules.plan.weekly import WeekPlan, build_tasks, plan_week
from careerhub.profile.models import WEEKDAYS, PlanSettings, WorkingHours

DAYS = 7


def working_windows(first_day: date, zone: ZoneInfo, hours: WorkingHours) -> list[Interval]:
    """Return the working window of each of the next `DAYS` days (a day off has none)."""
    windows: list[Interval] = []
    for offset in range(DAYS):
        day = first_day + timedelta(days=offset)
        window = hours.for_day(WEEKDAYS[day.weekday()])
        if window is not None:
            windows += daily_windows(day, 1, zone, *window)
    return windows


def suggest_week(
    session: Session,
    settings: Settings,
    budget_hours: float,
    now: datetime,
    plan: PlanSettings | None = None,
) -> WeekPlan:
    """Plan the next seven days. Only free time from now on is used; nothing is sent or saved."""
    plan = plan or PlanSettings()
    zone = ZoneInfo(settings.timezone)
    today = now.astimezone(zone).date()
    windows = [
        Interval(max(window.start, now), window.end)
        for window in working_windows(today, zone, plan.working_hours)
        if window.end > now
    ]
    if not windows:
        return WeekPlan(int(budget_hours * 60))
    horizon_start, horizon_end = windows[0].start, windows[-1].end
    busy: list[Interval] = []
    for name in settings.calendar_files:
        calendar = IcsCalendar.from_file(Path(name).expanduser(), zone)
        busy += calendar.busy(horizon_start, horizon_end)
    problems: list[str] = []
    if settings.use_apple_calendar:
        try:
            apple = AppleCalendar(open_event_kit_store(), settings.apple_calendars)
            busy += apple.busy(horizon_start, horizon_end)
        except CalendarUnavailable as error:
            problems.append(str(error))  # fall back to the other sources, and say so
    free = free_windows(busy, windows, plan.min_gap_minutes)
    tasks = build_tasks(session, zone, plan)
    result = plan_week(tasks, free, int(budget_hours * 60), zone, plan.daily_share)
    result.problems.extend(problems)
    return result
