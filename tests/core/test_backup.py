from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from careerhub.core.backup import backup_database, backups_dir, list_backups
from careerhub.core.migrate import migrate
from careerhub.core.models import Company
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory

NOON = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def key() -> str:
    return new_database_key()


@pytest.fixture
def engine(tmp_path: Path, key: str) -> Engine:
    db = open_engine(tmp_path / "live.db", key)
    migrate(db)
    with session_factory(db)() as session:
        session.add(Company(name="Backup Marker Co", name_key="backup marker co"))
        session.commit()
    return db


def test_backup_is_encrypted_and_restorable_with_the_same_key(
    engine: Engine, tmp_path: Path, key: str
) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    copy = backup_database(engine, folder=folder, now=NOON)
    data = copy.read_bytes()
    assert not data.startswith(b"SQLite format 3")
    assert b"Backup Marker Co" not in data
    restored = open_engine(copy, key)
    with session_factory(restored)() as session:
        assert session.query(Company).one().name == "Backup Marker Co"


def test_only_the_newest_backups_are_kept(engine: Engine, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(4):
        backup_database(engine, keep=2, folder=folder, now=NOON + timedelta(days=day))
    names = [path.name for path in list_backups(folder)]
    assert names == ["careerhub-20261022T120000Z.db", "careerhub-20261023T120000Z.db"]


def test_files_this_module_did_not_name_are_never_removed(engine: Engine, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine")
    for day in range(3):
        backup_database(engine, keep=1, folder=folder, now=NOON + timedelta(days=day))
    assert (folder / "notes.txt").read_text() == "mine"


def test_a_backup_never_overwrites_another(engine: Engine, tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    backup_database(engine, folder=folder, now=NOON)
    with pytest.raises(FileExistsError):
        backup_database(engine, folder=folder, now=NOON)


def test_keep_must_be_positive(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        backup_database(engine, keep=0, folder=tmp_path)


def test_default_folder_is_private_and_inside_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert backups_dir() == tmp_path / "backups"
    assert (tmp_path / "backups").stat().st_mode & 0o777 == 0o700


def test_backup_file_is_private(engine: Engine, tmp_path: Path) -> None:
    copy = backup_database(engine, folder=tmp_path, now=NOON)
    assert copy.stat().st_mode & 0o777 == 0o600
