"""Applications the user is tracking, saved views of them, and a CSV export (CH-142).

Career Hub never applies for anything: the user applies, then marks the job Applied (here or by the
Applied reaction) and the application is recorded. Nothing here sends or submits.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import (
    APPLICATION_STATUSES,
    Application,
    Interaction,
    Job,
    Person,
    utcnow,
)
from careerhub.modules.network import warm_paths

#: Saved views, as (key, label). "all" shows every application.
VIEWS = (
    ("all", "All"),
    ("followups_due", "Follow-ups due"),
    ("applied_no_reply", "Applied, no reply in 10+ days"),
    ("interviewing", "Interviewing"),
    ("deadlines_week", "Deadlines in the next 7 days"),
    ("interested_stale", "Interested but not applied for over a week"),
    ("warm_path", "Has a warm path"),
    ("interested", "Interested"),
)
NO_REPLY_DAYS = 10
STALE_INTEREST_DAYS = 7
STATUS_LABELS = {
    "interested": "Interested",
    "reached_out": "Reached out or referred",
    "applied": "Applied",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
    "ghosted": "Ghosted",
}


#: How far along a status is; a reaction may move an application forward, never back.
_RANK = {
    "interested": 0, "reached_out": 1, "applied": 2, "interviewing": 3, "offer": 4,
    "rejected": 5, "withdrawn": 5, "ghosted": 5,
}  # fmt: skip
#: Statuses where nothing more is due (no follow-up or deadline to chase).
CLOSED_STATUSES = frozenset({"rejected", "withdrawn", "ghosted"})


class TrackerError(ValueError):
    """The change can't be made; the message says why in plain words."""


@dataclass(frozen=True)
class TrackerRow:
    """One application as the Tracker lists it."""

    application_id: int
    job_id: int
    company: str
    title: str
    status: str
    applied_at: datetime | None
    follow_up_due: datetime | None
    next_step: str | None
    deadline: str | None


def track(
    session: Session,
    job_id: int,
    status: str,
    now: datetime | None = None,
    forward_only: bool = False,
    commit: bool = True,
) -> Application:
    """Start tracking a job, or change its status. "Applied" stamps the date the first time.

    With `forward_only` (a reaction on a card) the status never moves back: clicking Interested on
    a job you are interviewing for leaves it Interviewing.
    """
    if status not in APPLICATION_STATUSES:
        raise TrackerError(f"Unknown status {status!r}.")
    if session.get(Job, job_id) is None:
        raise TrackerError("That job no longer exists.")
    now = now or utcnow()
    application = session.scalars(select(Application).where(Application.job_id == job_id)).first()
    if application is None:
        application = Application(job_id=job_id, status=status)
        session.add(application)
    if not (forward_only and _RANK[application.status] >= _RANK[status]):
        application.status = status
    if application.status == "applied" and application.applied_at is None:
        application.applied_at = now
    if commit:
        session.commit()
    return application


def sync_with_reaction(
    session: Session, job_id: int, newest: str | None, commit: bool = True
) -> None:
    """Keep the tracker in line with the newest reaction to a job.

    Interested or Applied start tracking (never moving a status back). If the newest reaction is
    now "not interested", or none is left (an undo), an application that only the reaction created
    (still interested or applied, nothing of yours added to it) is removed.
    """
    if newest in ("interested", "applied"):
        track(session, job_id, newest, forward_only=True, commit=False)
    else:
        application = session.scalars(
            select(Application).where(Application.job_id == job_id)
        ).first()
        untouched = application is not None and not any(
            (application.next_step, application.follow_up_due, application.referrer,
             application.resume_variant)
        )  # fmt: skip
        if (
            application is not None
            and untouched
            and application.status in ("interested", "applied")
        ):
            session.delete(application)
    if commit:
        session.commit()


def set_follow_up(session: Session, application_id: int, due: datetime | None) -> Application:
    """Set (or clear, with None) the follow-up date of an application."""
    application = session.get(Application, application_id)
    if application is None:
        raise TrackerError("That application no longer exists.")
    application.follow_up_due = due
    session.commit()
    return application


def set_next_step(session: Session, application_id: int, text: str) -> Application:
    """Set the next step (a few words the user wrote); an empty text clears it."""
    application = session.get(Application, application_id)
    if application is None:
        raise TrackerError("That application no longer exists.")
    application.next_step = " ".join(text.split())[:500] or None
    session.commit()
    return application


def _deadline(job: Job) -> str | None:
    value = ((job.structured_fields or {}).get("fields") or {}).get("deadline")
    return value if isinstance(value, str) else None


def _no_reply(session: Session, application: Application, job: Job) -> bool:
    """Say whether nobody at the company has been logged as in touch since the user applied."""
    if application.applied_at is None:
        return False
    return (
        session.scalars(
            select(Interaction.id)
            .join(Person, Person.id == Interaction.person_id)
            .where(
                Person.company_id == job.company_id,
                Interaction.occurred_at > application.applied_at,
            )
            .limit(1)
        ).first()
        is None
    )


def tracker_rows(
    session: Session,
    view: str = "all",
    text: str = "",
    statuses: Collection[str] = (),
    now: datetime | None = None,
) -> list[TrackerRow]:
    """List applications in a saved `view`, narrowed by `text` (company or title) and `statuses`."""
    if view not in dict(VIEWS):
        raise TrackerError(f"Unknown view {view!r}.")
    now = now or utcnow()
    wanted = text.strip().casefold()
    rows: list[TrackerRow] = []
    query = select(Application, Job).join(Job, Job.id == Application.job_id)
    for application, job in session.execute(query.order_by(Application.id)):
        if statuses and application.status not in statuses:
            continue
        if wanted and wanted not in f"{job.company.name} {job.title}".casefold():
            continue
        if not _in_view(session, view, application, job, now):
            continue
        rows.append(
            TrackerRow(
                application.id,
                job.id,
                job.company.name,
                job.title,
                application.status,
                application.applied_at,
                application.follow_up_due,
                application.next_step,
                _deadline(job),
            )
        )
    return rows


def _in_view(session: Session, view: str, a: Application, job: Job, now: datetime) -> bool:
    if view == "all":
        return True
    if view == "followups_due":
        return (
            a.follow_up_due is not None
            and a.follow_up_due <= now
            and a.status not in CLOSED_STATUSES
        )
    if view == "applied_no_reply":
        return (
            a.status == "applied"
            and a.applied_at is not None
            and a.applied_at <= now - timedelta(days=NO_REPLY_DAYS)
            and _no_reply(session, a, job)
        )
    if view == "interviewing":
        return a.status == "interviewing"
    if view == "deadlines_week":
        if a.status in CLOSED_STATUSES:
            return False
        try:
            due = date.fromisoformat((_deadline(job) or "")[:10])  # "...T17:00" is that day
        except ValueError:
            return False
        return now.date() <= due <= (now + timedelta(days=7)).date()
    if view == "interested_stale":
        return a.status == "interested" and a.created_at <= now - timedelta(
            days=STALE_INTEREST_DAYS
        )
    if view == "warm_path":
        return bool(warm_paths(session, job.company_id, limit=1, now=now))
    return a.status == "interested"  # "interested"


_RISKY = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: object) -> str:
    """Make a cell safe to open in a spreadsheet: text starting like a formula gets a quote."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_RISKY) else text


def export_csv(rows: list[TrackerRow]) -> str:
    """Return `rows` as CSV text (company, role, status, applied, follow-up, next step)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Company", "Role", "Status", "Applied", "Follow-up", "Next step"])
    for row in rows:
        writer.writerow(
            [
                _safe_cell(row.company),
                _safe_cell(row.title),
                STATUS_LABELS.get(row.status, row.status),
                row.applied_at.date().isoformat() if row.applied_at else "",
                row.follow_up_due.date().isoformat() if row.follow_up_due else "",
                _safe_cell(row.next_step),
            ]
        )
    return out.getvalue()
