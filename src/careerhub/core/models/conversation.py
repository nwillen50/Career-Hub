"""Messages in the conversation panel (CH-158): what the user asked and what Career Hub answered."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin

ROLES = ("user", "assistant")


class ConversationMessage(TimestampMixin, Base):
    """One message. Stored locally in the encrypted database, like everything personal."""

    __tablename__ = "conversation_message"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        Index("ix_conversation_message_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    #: The job the question was asked about (from a job's page), or None for a general question.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"))
