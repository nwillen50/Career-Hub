"""The user's reactions to jobs: interested, not interested (with a reason), applied (CH-140).

A reaction is saved in the `feedback` table; the newest one for a job is the one that counts. It
can be undone for a few seconds. Reactions hide a job from Today and are counted for the shortlist
precision metric; they never change a stored score. (They will feed the interest model that picks
exploration jobs, which is not built yet.)
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from careerhub.core.models import REACTIONS, REASONS, Feedback, Job, utcnow
from careerhub.modules.jobs.applications import sync_with_reaction

#: How long after reacting the user may undo it.
UNDO_SECONDS = 10


class ReactionError(ValueError):
    """The reaction can't be saved; the message says why in plain words."""


def react(
    session: Session,
    job_id: int,
    reaction: str,
    reason: str | None = None,
) -> Feedback:
    """Save a reaction to a job. A reason is allowed only with "not_interested"."""
    if reaction not in REACTIONS:
        raise ReactionError(f"Unknown reaction {reaction!r}.")
    if reason is not None and reason != "":
        if reaction != "not_interested":
            raise ReactionError("Only 'not interested' takes a reason.")
        if reason not in REASONS:
            raise ReactionError(f"Unknown reason {reason!r}.")
    if session.get(Job, job_id) is None:
        raise ReactionError("That job no longer exists.")
    row = Feedback(job_id=job_id, reaction=reaction, reason=reason or None)
    session.add(row)
    session.flush()
    # one commit for the reaction and the tracker change, so they never disagree (CH-142)
    sync_with_reaction(session, job_id, reaction, commit=False)
    session.commit()
    return row


def undo(
    session: Session, job_id: int, feedback_id: int | None = None, now: datetime | None = None
) -> bool:
    """Take back one reaction to a job if it was made within `UNDO_SECONDS`.

    The card's Undo button carries the id of the reaction it made, so it can only take back that
    one (not a newer reaction from another tab). Without an id, the newest reaction is meant.
    """
    now = now or utcnow()
    query = select(Feedback).where(Feedback.job_id == job_id)
    if feedback_id is not None:
        query = query.where(Feedback.id == feedback_id)
    target = session.scalars(query.order_by(Feedback.id.desc()).limit(1)).first()
    if target is None or now - target.created_at > timedelta(seconds=UNDO_SECONDS):
        return False
    session.execute(delete(Feedback).where(Feedback.id == target.id))
    session.flush()
    session.expire_all()
    sync_with_reaction(session, job_id, current_reaction(session, job_id), commit=False)
    session.commit()
    return True


def reacted_job_ids(session: Session) -> set[int]:
    """Return the ids of jobs the user has reacted to (Today hides them)."""
    return set(session.scalars(select(Feedback.job_id).distinct()))


def settled_job_ids(session: Session) -> set[int]:
    """Return jobs whose newest reaction is "applied" or "not interested" (nothing left to plan).

    A job marked only "interested" is not settled: the user may still want to apply.
    """
    newest = select(func.max(Feedback.id)).group_by(Feedback.job_id)
    return set(
        session.scalars(
            select(Feedback.job_id).where(
                Feedback.id.in_(newest), Feedback.reaction.in_(("applied", "not_interested"))
            )
        )
    )


def current_reaction(session: Session, job_id: int) -> str | None:
    """Return the newest reaction to a job, or None."""
    return session.scalars(
        select(Feedback.reaction)
        .where(Feedback.job_id == job_id)
        .order_by(Feedback.id.desc())
        .limit(1)
    ).first()


def reaction_counts(session: Session, job_ids: Collection[int] | None = None) -> dict[str, int]:
    """Count jobs by their current reaction (the newest one per job), for the precision metric.

    The PRD's goal is that at least half of each day's top 5 are marked "interested"; "applied"
    counts as interested too.
    """
    newest = select(func.max(Feedback.id)).group_by(Feedback.job_id)
    query = select(Feedback.reaction, func.count()).where(Feedback.id.in_(newest))
    if job_ids is not None:
        query = query.where(Feedback.job_id.in_(job_ids))
    counts = {reaction: 0 for reaction in REACTIONS}
    for reaction, number in session.execute(query.group_by(Feedback.reaction)):
        counts[reaction] = number
    return counts
