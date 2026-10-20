"""Encrypted backups of the database.

`backup_database` writes a consistent copy with SQLite's `VACUUM INTO`, which works while the app
is running and keeps the copy encrypted with the same key (ADR-0001). Backups live in a
`backups` folder inside the data folder and are named `careerhub-<UTC date and time>.db`.

Keeping only the newest N removes older files, but only files this module named itself in its own
backups folder; nothing else is ever touched.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine

from careerhub.core.models import utcnow
from careerhub.core.paths import data_dir

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
