"""The weekly plan: suggested work blocks placed in free time, within the weekly time budget.

Pure code, no model. It only *suggests*: nothing is written to a calendar or sent anywhere (putting
blocks on the calendar after the user approves is a later feature).

Rules:

- Tasks are placed most urgent first: soonest deadline, then highest fit score.
- Each task goes in the earliest free gap that can hold it, and never past its deadline.
- Total planned time never exceeds the weekly budget, and no single day gets more than half of it,
  so work spreads across the week instead of piling into one day.
- A task that doesn't fit is listed in `unplaced` with the reason, never dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from sqlalchemy.orm import Session

from careerhub.modules.jobs.scoring import ranked_jobs
from careerhub.modules.network import warm_paths
from careerhub.modules.plan.freebusy import Interval

#: How long an application takes, and an outreach message.
APPLY_MINUTES = 90
OUTREACH_MINUTES = 30
#: No day gets more than this share of the weekly budget.
DAILY_SHARE = 0.5


@dataclass(frozen=True)
class PlanTask:
    """Something worth doing this week."""

    title: str
    minutes: int
    #: Higher is more important (the job's fit score).
    priority: float = 0.0
    due: datetime | None = None


@dataclass(frozen=True)
class PlannedBlock:
    """A task placed at a time."""

    task: PlanTask
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Unplaced:
    """A task that couldn't be scheduled, and why."""

    task: PlanTask
    reason: str


@dataclass
class WeekPlan:
    """The result: blocks in time order, what didn't fit, and the budget numbers."""

    budget_minutes: int
    blocks: list[PlannedBlock] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)

    @property
    def planned_minutes(self) -> int:
        """Total minutes in the blocks."""
        return sum(int((b.end - b.start).total_seconds() // 60) for b in self.blocks)


def plan_week(
    tasks: list[PlanTask], free: list[Interval], budget_minutes: int, zone: tzinfo
) -> WeekPlan:
    """Place `tasks` into the `free` gaps without passing the budget. See the module rules."""
    plan = WeekPlan(budget_minutes)
    gaps = sorted(free, key=lambda gap: gap.start)
    cursors = [gap.start for gap in gaps]
    per_day: dict[date, int] = {}
    daily_cap = int(budget_minutes * DAILY_SHARE)
    ordered = sorted(
        tasks, key=lambda t: (t.due is None, t.due or datetime.max.replace(tzinfo=UTC), -t.priority)
    )
    for task in ordered:
        if plan.planned_minutes + task.minutes > budget_minutes:
            plan.unplaced.append(Unplaced(task, "the weekly time budget is used up"))
            continue
        spot = _find_spot(task, gaps, cursors, per_day, daily_cap, zone)
        if isinstance(spot, str):
            plan.unplaced.append(Unplaced(task, spot))
            continue
        index, start = spot
        end = start + timedelta(minutes=task.minutes)
        cursors[index] = end
        day = start.astimezone(zone).date()
        per_day[day] = per_day.get(day, 0) + task.minutes
        plan.blocks.append(PlannedBlock(task, start, end))
    plan.blocks.sort(key=lambda block: block.start)
    return plan


def _find_spot(
    task: PlanTask,
    gaps: list[Interval],
    cursors: list[datetime],
    per_day: dict[date, int],
    daily_cap: int,
    zone: tzinfo,
) -> tuple[int, datetime] | str:
    """Return (gap index, start time) for the earliest fit, or the reason there is none."""
    reason = "no free time is long enough"
    need = timedelta(minutes=task.minutes)
    for index, gap in enumerate(gaps):
        start = cursors[index]
        if gap.end - start < need:
            continue
        if task.due is not None and start + need > task.due:
            reason = "no free time before its deadline"
            continue
        day = start.astimezone(zone).date()
        if per_day.get(day, 0) + task.minutes > daily_cap:
            reason = "the free time left is on days that already have enough planned"
            continue
        return index, start
    return reason


def end_of_day(day: date, zone: tzinfo) -> datetime:
    """Return the last instant of `day` in `zone`, as a UTC time (for deadlines)."""
    return datetime.combine(day, time(23, 59), zone).astimezone(UTC)


def build_tasks(session: Session, zone: tzinfo, limit: int = 5) -> list[PlanTask]:
    """Turn the shortlist into tasks: apply to each job, and reach out first if you know someone."""
    tasks: list[PlanTask] = []
    for job, assessment in ranked_jobs(session, limit):
        score = assessment.fit_score or 0.0
        due = _deadline(job.structured_fields, zone)
        label = f"{job.title} at {job.company.name}"
        tasks.append(PlanTask(f"Apply: {label}", APPLY_MINUTES, score, due))
        contacts = [p for p in warm_paths(session, job.company_id, limit=1) if p.strength > 0]
        if contacts:
            # Outreach before applying, so a referral can still help; due a day earlier.
            early = due - timedelta(days=1) if due else None
            title = f"Message {contacts[0].name} about {job.company.name}"
            tasks.append(PlanTask(title, OUTREACH_MINUTES, score + 1, early))
    return tasks


def _deadline(structured: dict[str, object], zone: tzinfo) -> datetime | None:
    """Read the extracted application deadline (a YYYY-MM-DD string), if there is one."""
    fields = structured.get("fields")
    raw = fields.get("deadline") if isinstance(fields, dict) else None
    try:
        return end_of_day(date.fromisoformat(str(raw)), zone) if raw else None
    except ValueError:
        return None
