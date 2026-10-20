"""The shared core tables: Company, Person, Interaction, Source, Proposal, and RunLog.

These are the entities more than one build step needs. Jobs-specific tables (Job, Assessment,
Application, ...) are added by the steps that first use them (ADR-0002).

Privacy: these tables hold personal data, so they live only in the encrypted database. Free-text
columns (`notes`, `summary`, `error_summary`) must never be copied into logs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careerhub.core.models.base import Base, TimestampMixin, UTCDateTime


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """Return a SQL condition limiting `column` to the given words."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


#: A source starts as `proposed`; only the user moves it to `approved` (never automatic).
SOURCE_STATUSES = ("proposed", "approved", "rejected", "disabled")
#: A proposal is something Career Hub suggests; it acts only after the user accepts.
PROPOSAL_STATUSES = ("pending", "accepted", "dismissed")
RUN_STATUSES = ("running", "succeeded", "failed")


class Company(TimestampMixin, Base):
    """An employer. `name_key` is the lowercase, trimmed name used to avoid duplicates."""

    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), unique=True)
    domain: Mapped[str | None] = mapped_column(String(253))
    notes: Mapped[str | None] = mapped_column(Text)

    people: Mapped[list[Person]] = relationship(back_populates="company")


class Person(TimestampMixin, Base):
    """Someone in the user's network, such as a LinkedIn connection or an alum."""

    __tablename__ = "person"
    __table_args__ = (Index("ix_person_company_id", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id"))
    full_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    #: Where we learned about this person, e.g. "linkedin_export" or "alumni_list".
    origin: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company | None] = relationship(back_populates="people")
    interactions: Mapped[list[Interaction]] = relationship(back_populates="person")


class Interaction(TimestampMixin, Base):
    """One contact with a person (a message, call, or meeting), logged by or for the user."""

    __tablename__ = "interaction"
    __table_args__ = (Index("ix_interaction_person_id", "person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    #: How it happened, e.g. "email", "linkedin", "call", "in_person".
    channel: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str | None] = mapped_column(Text)

    person: Mapped[Person] = relationship(back_populates="interactions")


class Source(TimestampMixin, Base):
    """A place jobs come from, such as one company's Greenhouse board.

    Career Hub may propose a source, but it is only used after the user approves it.
    """

    __tablename__ = "source"
    __table_args__ = (CheckConstraint(_one_of("status", SOURCE_STATUSES), name="status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str | None] = mapped_column(Text)


class Proposal(TimestampMixin, Base):
    """Something Career Hub suggests and the user decides on (a new source, a draft, a task).

    A proposal never acts by itself: `status` moves from `pending` only when the user decides.
    """

    __tablename__ = "proposal"
    __table_args__ = (CheckConstraint(_one_of("status", PROPOSAL_STATUSES), name="status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RunLog(TimestampMixin, Base):
    """One run of a pipeline step. `error_summary` must already have personal details removed."""

    __tablename__ = "run_log"
    __table_args__ = (CheckConstraint(_one_of("status", RUN_STATUSES), name="status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    step: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    items_processed: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
