"""What each model call cost (CH-51, ADR-0006): one row per call that reached a provider."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin


class LLMUsage(TimestampMixin, Base):
    """Tokens and estimated dollars for one model call. No prompt or response text is stored."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The pipeline run this call belonged to, if any.
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run_log.id"))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    #: Estimated US dollars; 0 for local models.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
