"""Encrypted backups of the database.

`backup_database` writes a consistent copy with SQLite's `VACUUM INTO`, which works while the app
is running and keeps the copy encrypted with the same key (ADR-0001). Backups live in a
`backups` folder inside the data folder and are named `careerhub-<UTC date and time>.db`.

Keeping only the newest N removes older files, but only files this module named itself in its own
backups folder; nothing else is ever touched.

`restore_backup` copies a backup into a NEW folder, opens the copy with the key, and compares row
counts with the backup itself. It never writes over the live database (CH-166).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine

from careerhub.core.models import utcnow
from careerhub.core.paths import data_dir
from careerhub.core.storage import open_engine

_NAME = re.compile(r"^careerhub-\d{8}T\d{6}Z\.db$")


def backups_dir() -> Path:
    """Return the backups folder inside the data folder, created private if needed."""
    folder = data_dir() / "backups"
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    return folder


def list_backups(folder: Path | None = None) -> list[Path]:
    """Return this module's backup files in `folder`, oldest first."""
    folder = folder or backups_dir()
    return sorted(path for path in folder.iterdir() if _NAME.match(path.name))


def backup_database(
    engine: Engine,
    keep: int = 7,
    folder: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Write an encrypted backup, remove all but the newest `keep`, and return the new file."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    folder = folder or backups_dir()
    stamp = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"careerhub-{stamp}.db"
    if target.exists():
        raise FileExistsError(f"A backup named {target.name} already exists.")
    with engine.connect() as connection:
        connection.exec_driver_sql("VACUUM INTO ?", (str(target),))
    target.chmod(0o600)
    for old in list_backups(folder)[:-keep]:
        old.unlink()
    return target


class RestoreError(Exception):
    """A backup could not be restored. The message never includes row contents."""


@dataclass(frozen=True)
class RestoreReport:
    """What `restore_backup` made: the new file and how many rows each table holds."""

    path: Path
    counts: dict[str, int]


def _table_counts(engine: Engine) -> dict[str, int]:
    """Return the row count of every table, or raise RestoreError if the file is damaged."""
    with engine.connect() as connection:
        check = connection.exec_driver_sql("PRAGMA integrity_check").scalars().all()
        if check != ["ok"]:
            raise RestoreError("The backup failed SQLite's integrity check.")
        names = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).scalars()
        return {
            name: int(connection.exec_driver_sql(f'SELECT count(*) FROM "{name}"').scalar_one())
            for name in list(names)
        }


def restore_backup(backup: Path, to_folder: Path, key: str) -> RestoreReport:
    """Copy `backup` into the new folder `to_folder` and check the copy.

    `to_folder` must not exist yet, so a restore can never replace the live database. The copy is
    opened with `key`, its integrity is checked, and its row counts must equal the backup's.
    On any failure the new folder is removed again.
    """
    if not backup.is_file():
        raise RestoreError(f"{backup.name} is not a file.")
    if to_folder.exists():
        raise RestoreError("The target folder already exists; choose a new folder.")
    to_folder.mkdir(mode=0o700, parents=True)
    target = to_folder / "careerhub.db"
    try:
        shutil.copyfile(backup, target)
        target.chmod(0o600)
        expected = _table_counts(open_engine(backup, key))
        restored = _table_counts(open_engine(target, key))
        if restored != expected:
            raise RestoreError("The restored copy does not match the backup.")
    except Exception:
        shutil.rmtree(to_folder, ignore_errors=True)
        raise
    return RestoreReport(path=target, counts=restored)
