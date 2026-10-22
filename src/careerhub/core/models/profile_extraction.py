"""Facts a model read from the user's resume or transcript (CH-91).

One row per (document kind, file hash, prompt version). Rows start `pending`: they are proposals
the user reviews (PRD, "inferred, with a proposal"), and nothing counts until accepted (CH-110).
The facts are Personal data, so this table lives only in the encrypted database.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin


class ProfileExtraction(TimestampMixin, Base):
    """Structured facts extracted from one version of one document."""

    __tablename__ = "profile_extraction"
    __table_args__ = (
        UniqueConstraint("kind", "file_sha256", "prompt_version", name="uq_profile_extraction"),
        CheckConstraint("kind IN ('resume', 'transcript')", name="kind_valid"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    #: SHA-256 of the file's bytes: a changed file means a new extraction.
    file_sha256: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column()
    #: The validated `ProfileFacts`, as JSON.
    facts: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    model_used: Mapped[str | None] = mapped_column(String(100))
