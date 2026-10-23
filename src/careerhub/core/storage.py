"""Opens Career Hub's encrypted database. This is the only code that does.

The database is SQLite encrypted with SQLCipher (ADR-0001). Every connection sets the key first,
then a few settings:

- ``foreign_keys = ON`` so references between tables are enforced
- ``journal_mode = WAL`` so reading while the pipeline writes doesn't block
- ``busy_timeout`` so a second process waits briefly instead of failing at once
- SQLCipher's own logging off, so a wrong key doesn't print to the terminal

Typical use::

    engine = open_database(KeychainKeyStore())
    Session = session_factory(engine)
    with Session() as session:
        ...
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import sqlcipher3
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from careerhub.core.paths import database_path
from careerhub.core.secrets import KeyStore, get_or_create_database_key, is_valid_database_key

#: How long a connection waits for another process's lock before giving up, in milliseconds.
BUSY_TIMEOUT_MS = 5_000

# Only the user may read or write the database file (rw-------).
_PRIVATE_FILE_MODE = 0o600


class StorageError(Exception):
    """Base class for problems opening the database."""


class WrongKeyError(StorageError):
    """The key doesn't open this database (or the file isn't a Career Hub database)."""


def open_engine(path: Path, key: str, one_connection_per_use: bool = False) -> Engine:
    """Return a SQLAlchemy engine for the encrypted database at ``path``.

    The file is created if it doesn't exist. The key is checked on every new connection, so a
    wrong key fails here rather than on the first query.

    By default one connection is kept per thread; ``one_connection_per_use=True`` uses none kept
    (a web server's worker threads would otherwise leave connections the pool cannot close).

    Raises:
        ValueError: ``key`` is not a valid database key.
        WrongKeyError: the key doesn't open an existing database at ``path``.

    """
    if not is_valid_database_key(key):
        # The message never includes the key itself.
        raise ValueError("The database key must be 64 lowercase hexadecimal characters.")

    def connect() -> sqlcipher3.Connection:
        conn = sqlcipher3.connect(str(path))
        conn.execute("PRAGMA cipher_log_level = NONE")
        # x'...' passes the key as raw bytes, skipping the slow password-stretching step that
        # only human-chosen passwords need.
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.DatabaseError:
            conn.close()
            raise WrongKeyError(
                f"The database key doesn't open {path.name}. If the key in the Keychain was "
                "changed or replaced, restore it or restore the database from a backup."
            ) from None
        return conn

    pool: dict[str, Any] = {"poolclass": NullPool} if one_connection_per_use else {}
    engine = create_engine("sqlite://", creator=connect, module=sqlcipher3.dbapi2, **pool)

    @event.listens_for(engine, "connect")
    def _configure(dbapi_conn: sqlcipher3.Connection, _record: object) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        cursor.close()
        _make_private(path)

    # Open one connection now so the file exists (with private permissions) and a wrong key is
    # reported immediately.
    with engine.connect():
        pass
    return engine


def _make_private(path: Path) -> None:
    """Limit the database and SQLite's side files to the current user (rw-------).

    In WAL mode SQLite keeps two extra files next to the database, ``-wal`` and ``-shm``. They
    are created with default permissions, so they're tightened here along with the main file.
    """
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            os.chmod(candidate, _PRIVATE_FILE_MODE)


def open_database(
    store: KeyStore, path: Path | None = None, one_connection_per_use: bool = False
) -> Engine:
    """Open the user's database, creating the key and the file on first use.

    ``path`` defaults to ``careerhub.db`` in the data folder (see ``careerhub.core.paths``).
    ``one_connection_per_use`` opens a fresh connection for each session and closes it after, which
    a server answering requests on many threads needs.
    """
    key = get_or_create_database_key(store)
    return open_engine(path if path is not None else database_path(), key, one_connection_per_use)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a factory for ORM sessions on ``engine``.

    ``expire_on_commit=False`` keeps objects readable after a commit, which suits short
    pipeline steps that commit and then report what they wrote.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)
