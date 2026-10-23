"""The user's quick reactions to jobs (CH-140, PRD "Feedback")."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin

REACTIONS = ("interested", "not_interested", "applied")
#: Why a job is "not interested": the PRD's one-click reasons.
REASONS = ("level", "location", "field", "company", "pay")


class Feedback(TimestampMixin, Base):
    """One reaction to one job. The newest reaction for a job is the one that counts."""

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "reaction IN ('interested', 'not_interested', 'applied')", name="reaction_valid"
        ),
        Index("ix_feedback_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"))
    reaction: Mapped[str] = mapped_column(String(20))
    #: One of REASONS, only for "not_interested" and only if the user chose one.
    reason: Mapped[str | None] = mapped_column(String(20))
