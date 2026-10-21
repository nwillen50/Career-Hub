"""The Job table: one row per posting seen at a source (CH-59, ADR-0011).

The raw payload is kept next to the structured fields so extraction can be redone when prompts
improve (PRD). Postings are public data, but the table sits in the encrypted database anyway.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careerhub.core.models.base import Base, TimestampMixin, UTCDateTime
from careerhub.core.models.core_tables import Company

#: `new` while the posting is open; `stale` once it has been open unusually long or reposted
#: (set by a later ticket); `closed` when a full fetch no longer lists it.
JOB_STATUSES = ("new", "stale", "closed")


class Job(TimestampMixin, Base):
    """A job posting seen at a source."""

    __tablename__ = "job"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_job_source_posting"),
        CheckConstraint("status IN ('new', 'stale', 'closed')", name="status_valid"),
        Index("ix_job_dedupe_key", "dedupe_key"),
        Index("ix_job_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    #: The posting's id at the source (e.g. Greenhouse's job id), as text.
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    location: Mapped[str | None] = mapped_column(String(500))
    #: The posting text exactly as the source gave it.
    raw_text: Mapped[str | None] = mapped_column(Text)
    #: The source's full record for this posting.
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Fields extracted from the posting (step 5); empty until then.
    structured_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    #: Hash of company, title, and location: equal keys at different sources are the same job.
    dedupe_key: Mapped[str] = mapped_column(String(64))
    #: Set when this posting repeats an earlier one from another source.
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("job.id"))

    company: Mapped[Company] = relationship()
