"""An application the user is tracking for one job (CH-142, PRD "Application")."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin, UTCDateTime

#: The PRD's statuses, in the order a job usually moves through them.
APPLICATION_STATUSES = (
    "interested",
    "reached_out",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
    "ghosted",
)


class Application(TimestampMixin, Base):
    """One job the user is following. Career Hub never applies; the user does, then records it."""

    __tablename__ = "application"
    __table_args__ = (
        UniqueConstraint("job_id", name="one_application_per_job"),
        CheckConstraint(
            "status IN ('interested', 'reached_out', 'applied', 'interviewing', 'offer', "
            "'rejected', 'withdrawn', 'ghosted')",
            name="status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"))
    status: Mapped[str] = mapped_column(String(20), default="interested")
    #: When the user applied (set when the status first becomes "applied").
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resume_variant: Mapped[str | None] = mapped_column(String(200))
    referrer: Mapped[str | None] = mapped_column(String(200))
    next_step: Mapped[str | None] = mapped_column(Text)
    follow_up_due: Mapped[datetime | None] = mapped_column(UTCDateTime())
