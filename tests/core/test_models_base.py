from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.schema import CreateTable

from careerhub.core.models import NAMING_CONVENTION, Base, TimestampMixin, UTCDateTime, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory


class ScratchBase(DeclarativeBase):
    """A separate base, so test tables never leak into Career Hub's real metadata."""

    metadata = Base.metadata.__class__(naming_convention=NAMING_CONVENTION)


class Event(TimestampMixin, ScratchBase):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    happened_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Child(ScratchBase):
    __tablename__ = "child"
    __table_args__ = (UniqueConstraint("name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))


@pytest.fixture
def make_session(tmp_path: Path) -> sessionmaker[Session]:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    ScratchBase.metadata.create_all(engine)
    return session_factory(engine)


def test_utcnow_is_timezone_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_datetimes_come_back_in_utc(make_session: sessionmaker[Session]) -> None:
    eastern = timezone(timedelta(hours=-4))
    local = datetime(2026, 10, 19, 20, 0, tzinfo=eastern)
    with make_session() as s:
        s.add(Event(name="call", happened_at=local))
        s.commit()
    with make_session() as s:
        stored = s.scalars(select(Event.happened_at)).one()
    assert stored == local
    assert stored is not None and stored.tzinfo == UTC
    assert stored.hour == 0 and stored.day == 20


def test_naive_datetimes_are_refused(make_session: sessionmaker[Session]) -> None:
    with make_session() as s:
        s.add(Event(name="ambiguous", happened_at=datetime(2026, 10, 19, 20, 0)))
        with pytest.raises(Exception, match="must have a timezone"):
            s.commit()


def test_timestamps_are_set_and_updated(make_session: sessionmaker[Session]) -> None:
    with make_session() as s:
        event = Event(name="first")
        s.add(event)
        s.commit()
        created, updated = event.created_at, event.updated_at
        assert created.tzinfo == UTC and updated.tzinfo == UTC
        time.sleep(0.01)
        event.name = "renamed"
        s.commit()
        assert event.created_at == created
        assert event.updated_at > updated


def test_constraints_get_predictable_names() -> None:
    ddl = str(CreateTable(Child.__table__).compile())  # type: ignore[arg-type]
    assert "pk_child" in ddl
    assert "uq_child_name" in ddl
    assert "fk_child_event_id_event" in ddl
