"""Restoring a backup into a new folder and checking it (CH-166)."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from careerhub.cli import main
from careerhub.core.backup import RestoreError, backup_database, restore_backup
from careerhub.core.migrate import migrate
from careerhub.core.models import Company
from careerhub.core.secrets import DATABASE_KEY_NAME, InMemoryKeyStore, new_database_key
from careerhub.core.storage import WrongKeyError, open_engine, session_factory

NOON = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def key() -> str:
    return new_database_key()


@pytest.fixture
def backup(tmp_path: Path, key: str) -> Path:
    engine: Engine = open_engine(tmp_path / "live.db", key)
    migrate(engine)
    with session_factory(engine)() as session:
        session.add_all([Company(name=f"Co {n}", name_key=f"co {n}") for n in range(3)])
        session.commit()
    folder = tmp_path / "backups"
    folder.mkdir()
    return backup_database(engine, folder=folder, now=NOON)


def test_a_restored_backup_has_the_same_rows(backup: Path, tmp_path: Path, key: str) -> None:
    report = restore_backup(backup, tmp_path / "restored", key)
    assert report.counts["company"] == 3
    with session_factory(open_engine(report.path, key))() as session:
        assert session.query(Company).count() == 3
    assert stat.S_IMODE(report.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report.path.parent.stat().st_mode) == 0o700


def test_restore_never_goes_into_an_existing_folder(backup: Path, tmp_path: Path, key: str) -> None:
    existing = tmp_path / "live"
    existing.mkdir()
    (existing / "careerhub.db").write_text("precious")
    with pytest.raises(RestoreError, match="already exists"):
        restore_backup(backup, existing, key)
    assert (existing / "careerhub.db").read_text() == "precious"


def test_a_wrong_key_fails_and_leaves_no_folder_behind(backup: Path, tmp_path: Path) -> None:
    with pytest.raises(WrongKeyError):
        restore_backup(backup, tmp_path / "restored", new_database_key())
    assert not (tmp_path / "restored").exists()


def test_a_missing_backup_is_a_clear_error(tmp_path: Path, key: str) -> None:
    with pytest.raises(RestoreError, match="not a file"):
        restore_backup(tmp_path / "nope.db", tmp_path / "restored", key)
    assert not (tmp_path / "restored").exists()


def test_the_command_restores_and_prints_how_to_switch_over(
    backup: Path,
    tmp_path: Path,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    store = InMemoryKeyStore()
    store.set(DATABASE_KEY_NAME, key)
    code = main(["backup", "restore", str(backup), "--to", str(tmp_path / "new")], store=store)
    out = capsys.readouterr().out
    assert code == 0
    assert "tables," in out and "rows," in out
    assert "Nothing was changed in your live data" in out
    assert (tmp_path / "new" / "careerhub.db").exists()


def test_the_command_refuses_an_existing_folder(
    backup: Path,
    tmp_path: Path,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    store = InMemoryKeyStore()
    store.set(DATABASE_KEY_NAME, key)
    (tmp_path / "taken").mkdir()
    assert (
        main(["backup", "restore", str(backup), "--to", str(tmp_path / "taken")], store=store) == 1
    )
    assert "Restore failed" in capsys.readouterr().out


def test_backup_list_says_when_there_are_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert main(["backup", "list"], store=InMemoryKeyStore()) == 0
    assert "No backups yet" in capsys.readouterr().out


def test_restoring_never_changes_the_backup_or_leaves_side_files_beside_it(
    backup: Path, tmp_path: Path, key: str
) -> None:
    """Review: opening the backup as a database switched it to WAL and left -wal/-shm files."""
    import hashlib

    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    restore_backup(backup, tmp_path / "restored", key)
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in backup.parent.iterdir()) == [backup.name]


def test_a_dangling_symlink_target_is_a_clear_refusal(
    backup: Path, tmp_path: Path, key: str
) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    with pytest.raises(RestoreError, match="already exists"):
        restore_backup(backup, link, key)


def test_a_failed_restore_removes_the_parent_folders_it_made(backup: Path, tmp_path: Path) -> None:
    with pytest.raises(WrongKeyError):
        restore_backup(backup, tmp_path / "a" / "b" / "c", new_database_key())
    assert not (tmp_path / "a").exists()


def test_restore_does_not_create_a_key_when_the_keychain_has_none(
    backup: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    store = InMemoryKeyStore()
    code = main(["backup", "restore", str(backup), "--to", str(tmp_path / "new")], store=store)
    assert code == 1 and "no database key" in capsys.readouterr().out
    assert store.get(DATABASE_KEY_NAME) is None and not (tmp_path / "new").exists()
