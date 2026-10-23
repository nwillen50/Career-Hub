"""What the Plan tab shows (CH-143): the weekly plan, read-only, nothing written to any calendar.

The plan itself comes from `suggest_week` (ADR-0027); this module only arranges it for a page:
blocks grouped by day, how much of the time budget is used, which free/busy sources were used, and
any calendar problem in words. A calendar that cannot be read never breaks the page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from careerhub.core.models import utcnow
from careerhub.core.settings import Settings, load_settings
from careerhub.modules.plan import suggest_week
from careerhub.modules.plan.ics import IcsError
from careerhub.profile.files import load_preferences


@dataclass(frozen=True)
class BlockView:
    """One planned block as shown: when, what, and the deadline that made it urgent."""

    start: str
    end: str
    title: str
    minutes: int
    due: str | None


@dataclass(frozen=True)
class DayView:
    """The blocks of one day."""

    heading: str
    blocks: list[BlockView]


@dataclass(frozen=True)
class PlanView:
    """Everything the Plan tab shows."""

    days: list[DayView] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    planned_minutes: int = 0
    budget_minutes: int = 0
    sources: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def used(self) -> str:
        """Describe the budget use, such as "2h 30m of 5h planned"."""
        return f"{_hours(self.planned_minutes)} of {_hours(self.budget_minutes)} planned"


def _hours(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m" if rest else f"{hours}h"


def sources_used(settings: Settings) -> list[str]:
    """Say where free time came from: working hours, calendar files (names only), Apple Calendar."""
    parts = ["Working hours"]
    parts += [f"Calendar file {Path(name).name}" for name in settings.calendar_files]
    if settings.use_apple_calendar:
        parts.append("Apple Calendar (free and busy times only)")
    if len(parts) == 1:
        parts[0] = "Working hours only (no calendar is connected)"
    return parts


def gather_plan(session: Session, now: datetime | None = None) -> PlanView:
    """Build the plan for the next seven days as of `now`."""
    now = now or utcnow()
    settings = load_settings()
    preferences = load_preferences()
    budget = preferences.weekly_time_budget_hours if preferences else 5.0
    sources = sources_used(settings)
    try:
        plan = suggest_week(
            session, settings, budget, now, preferences.plan if preferences else None
        )
    except IcsError as error:
        return PlanView(
            budget_minutes=int(budget * 60),
            sources=sources,
            problems=[f"A calendar file could not be read, so no plan was made: {error}"],
        )
    zone = ZoneInfo(settings.timezone)
    days: dict[date, list[BlockView]] = {}
    for block in plan.blocks:
        start, end = block.start.astimezone(zone), block.end.astimezone(zone)
        due = block.task.due.astimezone(zone).strftime("%a %b %d") if block.task.due else None
        days.setdefault(start.date(), []).append(
            BlockView(
                start.strftime("%H:%M"),
                end.strftime("%H:%M"),
                block.task.title,
                int((block.end - block.start).total_seconds() // 60),
                due,
            )
        )
    return PlanView(
        days=[DayView(day.strftime("%A, %b %d"), days[day]) for day in sorted(days)],
        unplaced=[f"{item.task.title} ({item.reason})" for item in plan.unplaced],
        planned_minutes=plan.planned_minutes,
        budget_minutes=plan.budget_minutes,
        sources=sources,
        problems=list(plan.problems),
    )
