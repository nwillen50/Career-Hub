"""Records each run of a pipeline step in the `run_log` table.

Use `start_run` when a step begins and `finish_run` when it ends. If a run is interrupted, its row
stays `running`, which is how a later session can tell it never finished.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import RunLog, utcnow


def start_run(session: Session, step: str) -> RunLog:
    """Record that `step` has started and return its row (saved immediately)."""
    run = RunLog(step=step, status="running", started_at=utcnow())
    session.add(run)
    session.commit()
    return run


def finish_run(
    session: Session,
    run: RunLog,
    items_processed: int = 0,
    error: BaseException | None = None,
) -> RunLog:
    """Mark `run` finished: `succeeded`, or `failed` if `error` is given.

    Only the error's *type name* is stored, never its message, because messages can quote
    personal data. Full detail belongs in the (redacted) log.
    """
    run.finished_at = utcnow()
    run.items_processed = items_processed
    run.status = "failed" if error is not None else "succeeded"
    run.error_summary = type(error).__name__ if error is not None else None
    session.commit()
    return run


def recent_runs(session: Session, limit: int = 10) -> list[RunLog]:
    """Return the most recent runs, newest first."""
    query = select(RunLog).order_by(RunLog.started_at.desc(), RunLog.id.desc()).limit(limit)
    return list(session.scalars(query))
