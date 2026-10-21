"""The Assessment table: how one job fares against the user's constraints and rubric (CH-66).

Filters write `filter_passed` and `filter_results` (CH-66); scoring fills in the rest (CH-67).
One row per job; a rerun overwrites it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin


class Assessment(TimestampMixin, Base):
    """Filter results and fit score for one job."""

    __tablename__ = "assessment"
    __table_args__ = (UniqueConstraint("job_id", name="uq_assessment_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"))
    #: False when a hard constraint rules the job out.
    filter_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    #: {"reasons": [...], "uncertain": [...]}: why it failed, and what couldn't be checked.
    filter_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fit_score: Mapped[float | None] = mapped_column(Float)
    #: Per-criterion scores: [{"name": ..., "score": 0-10, "reason": ...}].
    criteria_scores: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    explanation: Mapped[str | None] = mapped_column(Text)
    #: Tailoring notes for the job: {"notes": {...}, "prompt_version": n, "resume_sha256": ...}.
    tailoring: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_used: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[int | None] = mapped_column()
    rubric_version: Mapped[int | None] = mapped_column()
