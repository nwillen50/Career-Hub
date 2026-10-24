"""Catch up a night that was skipped because the Mac was on battery (CH-181).

`hub nightly --if-plugged-in` skips on battery. That skip is recorded in the run log as step
`nightly_skip` (it is not a failed run). A second launchd job (`com.careerhub.catchup`) runs
`hub nightly --catch-up` every 30 minutes; `decide` says whether this is the moment to run the
full nightly once: tonight's run was skipped for battery, no nightly run has started since, none is
running now, the Mac is plugged in, and it is before the cutoff (`catchup_until`, local time).
Anything unclear means do nothing: an unreadable power source is handled by the main job, which
runs anyway. Reading the run log is the only lock: a nightly that is running (or ran since the
skip) blocks a catch-up, so two runs never overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import RunLog, utcnow
from careerhub.core.settings import Settings
from careerhub.schedule import parse_clock

SKIP_STEP = "nightly_skip"
NIGHTLY_STEP = "nightly"
#: A run still marked "running" after this long is taken to be dead, not in progress.
STALE_RUNNING = timedelta(hours=12)


@dataclass(frozen=True)
class CatchUpDecision:
    """Whether to run the nightly now, and the reason in plain words."""

    run: bool
    reason: str


def record_skip(session: Session, now: datetime | None = None) -> RunLog:
    """Record that tonight's run was skipped because the Mac is on battery."""
    moment = now or utcnow()
    row = RunLog(
        step=SKIP_STEP,
        status="succeeded",
        started_at=moment,
        finished_at=moment,
        error_summary="skipped: on battery",
    )
    session.add(row)
    session.commit()
    return row


def _latest(session: Session, step: str) -> RunLog | None:
    return session.scalars(
        select(RunLog)
        .where(RunLog.step == step)
        .order_by(RunLog.started_at.desc(), RunLog.id.desc())
    ).first()


def decide(session: Session, now: datetime, power: str, settings: Settings) -> CatchUpDecision:
    """Say whether to run the nightly now to catch up a battery skip."""
    zone = ZoneInfo(settings.timezone)
    local = now.astimezone(zone)
    skip = _latest(session, SKIP_STEP)
    if skip is None or skip.started_at.astimezone(zone).date() != local.date():
        return CatchUpDecision(False, "tonight's run was not skipped for battery")
    hour, minute = parse_clock(settings.catchup_until)
    if local.time() >= local.time().replace(hour=hour, minute=minute, second=0, microsecond=0):
        return CatchUpDecision(False, f"it is past the cutoff ({settings.catchup_until})")
    last = _latest(session, NIGHTLY_STEP)
    if last is not None and last.started_at >= skip.started_at:
        if last.status == "running" and now - last.started_at < STALE_RUNNING:
            return CatchUpDecision(False, "a run is in progress")
        return CatchUpDecision(False, "a run has already happened since the skip")
    if power != "ac":
        return CatchUpDecision(False, "the Mac is not plugged in" if power == "battery" else
                               "the power source could not be read")  # fmt: skip
    return CatchUpDecision(True, "plugged in after a skipped night")


def catchup_note(session: Session, zone: ZoneInfo) -> str | None:
    """Return the System status line for the latest skip, or None if there was none today or later.

    "Skipped on battery, caught up at 6:40", or "...; will catch up when plugged in".
    """
    skip = _latest(session, SKIP_STEP)
    if skip is None:
        return None
    ran = session.scalars(
        select(RunLog)
        .where(RunLog.step == NIGHTLY_STEP, RunLog.started_at >= skip.started_at)
        .order_by(RunLog.started_at)
    ).first()
    when = skip.started_at.astimezone(zone)
    shown = f"{when:%a %b %d}, {when:%H:%M}"
    if ran is None:
        return f"Skipped on battery at {shown}; no catch-up has run yet."
    started = ran.started_at.astimezone(zone)
    return f"Skipped on battery at {shown}, caught up at {started:%H:%M}."
