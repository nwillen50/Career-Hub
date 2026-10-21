"""The Plan module: free time and the weekly time budget become suggested work blocks."""

from careerhub.modules.plan.freebusy import (
    FreeBusySource,
    Interval,
    daily_windows,
    free_windows,
    merge,
)
from careerhub.modules.plan.ics import IcsCalendar
from careerhub.modules.plan.suggest import suggest_week
from careerhub.modules.plan.weekly import (
    PlannedBlock,
    PlanTask,
    Unplaced,
    WeekPlan,
    build_tasks,
    plan_week,
)

__all__ = [
    "FreeBusySource",
    "IcsCalendar",
    "Interval",
    "PlanTask",
    "PlannedBlock",
    "Unplaced",
    "WeekPlan",
    "build_tasks",
    "daily_windows",
    "free_windows",
    "merge",
    "plan_week",
    "suggest_week",
]
