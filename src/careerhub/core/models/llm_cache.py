"""The model router's response cache (CH-50, ADR-0005).

One row per distinct call. Only a hash of the prompt is stored, never the prompt itself; the
response text may contain personal data, so this table lives only in the encrypted database.
"""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerhub.core.models.base import Base, TimestampMixin


class LLMCacheEntry(TimestampMixin, Base):
    """A saved model answer, found again by provider, model, and prompt hash."""

    __tablename__ = "llm_cache"
    __table_args__ = (
        UniqueConstraint("provider", "model", "prompt_hash", name="uq_llm_cache_call"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    #: SHA-256 of the system text, prompt, and token limit (see `careerhub.llm.cache`).
    prompt_hash: Mapped[str] = mapped_column(String(64))
    response_text: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
