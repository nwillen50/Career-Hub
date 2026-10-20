"""The models and the migrations must never drift apart, and the core tables must behave."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError

from careerhub.core.migrate import migrate
from careerhub.core.models import (
    Base,
    Company,
    Interaction,
    Person,
    Proposal,
    RunLog,
    Source,
    utcnow,
)
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory

CORE_TABLES = {"company", "person", "interaction", "source", "proposal", "run_log"}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = open_engine(tmp_path / "careerhub.db", new_database_key())
    migrate(db)
    return db


def test_migrations_create_every_core_table(engine: Engine) -> None:
    assert CORE_TABLES <= set(inspect(engine).get_table_names())


def test_models_and_migrations_agree(engine: Engine) -> None:
    """If this fails, a model changed without a migration: run `migrate new` and read the file."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_every_table_is_in_the_metadata() -> None:
    assert CORE_TABLES <= set(Base.metadata.tables)


def test_people_interactions_and_times_round_trip(engine: Engine) -> None:
    when = datetime(2026, 10, 1, 15, 30, tzinfo=UTC)
    with session_factory(engine)() as session:
        company = Company(name="Acme", name_key="acme")
        person = Person(full_name="Sample Person", company=company)
        person.interactions.append(Interaction(occurred_at=when, channel="email"))
        session.add(company)
        session.commit()
        session.expire_all()
        loaded = session.get(Person, person.id)
        assert loaded is not None
        assert loaded.company is not None and loaded.company.name == "Acme"
        assert loaded.interactions[0].occurred_at == when


def test_company_names_are_unique(engine: Engine) -> None:
    with session_factory(engine)() as session:
        session.add_all(
            [Company(name="Acme", name_key="acme"), Company(name="ACME", name_key="acme")]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_status_columns_reject_unknown_words(engine: Engine) -> None:
    for row in (
        Source(kind="greenhouse", name="Acme", url="https://example.com/acme", status="bogus"),
        Proposal(kind="source", title="Add Acme", status="bogus"),
        RunLog(step="fetch", started_at=utcnow(), status="bogus"),
    ):
        with session_factory(engine)() as session:
            session.add(row)
            with pytest.raises(IntegrityError):
                session.commit()


def test_new_sources_and_proposals_wait_for_the_user(engine: Engine) -> None:
    with session_factory(engine)() as session:
        source = Source(kind="greenhouse", name="Acme", url="https://example.com/acme")
        proposal = Proposal(kind="source", title="Add Acme")
        session.add_all([source, proposal])
        session.commit()
        assert source.status == "proposed" and source.decided_at is None
        assert proposal.status == "pending" and proposal.decided_at is None


def test_migration_can_be_undone(tmp_path: Path) -> None:
    from alembic import command

    from careerhub.core import migrate as migrate_module

    db = open_engine(tmp_path / "down.db", new_database_key())
    migrate(db)
    config = migrate_module._config()
    with db.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert not CORE_TABLES & set(inspect(db).get_table_names())
