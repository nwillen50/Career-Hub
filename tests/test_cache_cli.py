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
