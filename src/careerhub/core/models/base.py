"""The building blocks every Career Hub table shares.

- `Base`: the parent class of every table. Its naming convention gives every constraint a
  predictable name, which SQLite migrations need (Alembic rebuilds SQLite tables to change them,
  and can only drop or alter a constraint it can name).
- `UTCDateTime`: a column type that only accepts timezone-aware datetimes and always returns them
  in UTC, so times are never ambiguous.
- `TimestampMixin`: adds `created_at` and `updated_at` to a table.
- `utcnow()`: the current time in UTC, timezone-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

#: How constraint names are built: e.g. "fk_person_company_id_company" for a foreign key.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Return the current time in UTC, with its timezone attached."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A datetime column that stores and returns UTC.

    SQLite has no timezone support, so values are stored without one and are always UTC.
    Writing a datetime with no timezone raises ValueError: guessing its timezone is exactly the
    kind of bug this type exists to prevent.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert a timezone-aware datetime to naive UTC for storage."""
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetimes must have a timezone; use careerhub.core.models.utcnow().")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Attach UTC to a stored datetime."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Parent class of every Career Hub table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds `created_at` (set once) and `updated_at` (set on every change), both in UTC."""

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )
