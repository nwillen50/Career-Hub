"""Tests for `hub cache clear` and the cache_ttl_days setting (CH-80)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import LLMCacheEntry, utcnow
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings
from careerhub.core.storage import open_database, session_factory


def seed(store: InMemoryKeyStore) -> None:
    """Save one fresh and one long-unused cache entry."""
    engine = open_database(store)
    migrate(engine)
    with session_factory(engine)() as session:
        for name, used in (("fresh", utcnow()), ("old", datetime(2020, 1, 1, tzinfo=UTC))):
            session.add(
                LLMCacheEntry(
                    provider="p", model="m", prompt_hash=name, response_text="x", last_used_at=used
                )
            )
        session.commit()


def count(store: InMemoryKeyStore) -> int:
    with session_factory(open_database(store))() as session:
        return session.scalar(select(func.count()).select_from(LLMCacheEntry)) or 0


def test_cache_clear_expired_removes_only_expired_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    seed(store)
    assert main(["cache", "clear", "--expired"], store) == 0
    assert "Removed 1 saved model answer." in capsys.readouterr().out
    assert count(store) == 1


def test_cache_clear_removes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    seed(store)
    assert main(["cache", "clear"], store) == 0
    assert "Removed 2 saved model answers." in capsys.readouterr().out
    assert count(store) == 0


def test_cache_ttl_days_defaults_to_30_and_is_validated() -> None:
    assert Settings().cache_ttl_days == 30
    for bad in (0, -1, 10_000):
        with pytest.raises(ValidationError):
            Settings(cache_ttl_days=bad)


def test_the_migration_gives_existing_cache_rows_a_last_used_time(tmp_path: Path) -> None:
    from alembic import command
    from sqlalchemy import text

    from careerhub.core.migrate import _config
    from careerhub.core.secrets import new_database_key
    from careerhub.core.storage import open_engine

    engine = open_engine(tmp_path / "old.db", new_database_key())
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "db9e23f5a551")  # the revision before last_used_at existed
        connection.execute(
            text(
                "INSERT INTO llm_cache (provider, model, prompt_hash, response_text, "
                "input_tokens, output_tokens, created_at, updated_at) "
                "VALUES ('p', 'm', 'h', 'x', 0, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    migrate(engine)
    with session_factory(engine)() as session:
        entry = session.scalars(select(LLMCacheEntry)).one()
        assert entry.last_used_at > datetime(2026, 1, 2, tzinfo=UTC)
