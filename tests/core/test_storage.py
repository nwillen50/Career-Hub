from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest
from sqlalchemy import String, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from careerhub.core import paths
from careerhub.core.secrets import DATABASE_KEY_NAME, InMemoryKeyStore, new_database_key
from careerhub.core.storage import (
    WrongKeyError,
    open_database,
    open_engine,
    session_factory,
)

PLAINTEXT_HEADER = b"SQLite format 3\x00"


class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "note"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(200))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "careerhub.db"


def test_write_and_read_an_encrypted_record(db_path: Path) -> None:
    engine = open_engine(db_path, new_database_key())
    Base.metadata.create_all(engine)
    Session = session_factory(engine)
    with Session() as session:
        session.add(Note(text="Applied to Acme"))
        session.commit()
    with Session() as session:
        assert session.scalars(select(Note.text)).one() == "Applied to Acme"


def test_file_is_encrypted_and_private(db_path: Path) -> None:
    engine = open_engine(db_path, new_database_key())
    Base.metadata.create_all(engine)
    with session_factory(engine)() as session:
        session.add(Note(text="MARKER-7731"))
        session.commit()
    engine.dispose()
    raw = db_path.read_bytes()
    assert not raw.startswith(PLAINTEXT_HEADER)
    assert b"MARKER-7731" not in raw
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(db_path).execute("SELECT count(*) FROM sqlite_master").fetchone()


def test_wrong_key_fails_when_opening_quietly_and_without_the_key(
    db_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    open_engine(db_path, new_database_key()).dispose()
    wrong = new_database_key()
    with pytest.raises(WrongKeyError) as caught:
        open_engine(db_path, wrong)
    assert wrong not in str(caught.value)
    out, err = capfd.readouterr()
    assert "ERROR CORE" not in out + err


@pytest.mark.parametrize("bad", ["", "short", "A" * 64, "z" * 64])
def test_malformed_keys_are_refused_before_touching_the_file(db_path: Path, bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        open_engine(db_path, bad)
    assert not db_path.exists()
    if bad:
        assert bad not in str(caught.value)


def test_connection_settings(db_path: Path) -> None:
    engine = open_engine(db_path, new_database_key())
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_foreign_keys_are_enforced(db_path: Path) -> None:
    engine = open_engine(db_path, new_database_key())
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER "
                "REFERENCES parent(id))"
            )
        )
    with pytest.raises(Exception, match="FOREIGN KEY"):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO child (parent_id) VALUES (99)"))


def test_engine_description_does_not_reveal_the_key(db_path: Path) -> None:
    key = new_database_key()
    engine = open_engine(db_path, key)
    assert key not in repr(engine)
    assert key not in str(engine.url)


def test_open_database_creates_key_and_file_on_first_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    store = InMemoryKeyStore()
    engine = open_database(store)
    assert store.get(DATABASE_KEY_NAME) is not None
    assert (tmp_path / "data" / "careerhub.db").exists()
    engine.dispose()
    # Opening again uses the same key and the same file.
    Base.metadata.create_all(open_database(store))


def test_open_database_with_an_explicit_path(db_path: Path) -> None:
    open_database(InMemoryKeyStore(), path=db_path).dispose()
    assert db_path.exists()


def test_sqlite_side_files_are_private_too(db_path: Path) -> None:
    engine = open_engine(db_path, new_database_key())
    Base.metadata.create_all(engine)
    with session_factory(engine)() as session:
        session.add(Note(text="written through the WAL"))
        session.commit()
    side_files = [Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
    assert all(p.exists() for p in side_files)
    for p in [db_path, *side_files]:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name


def test_a_connection_per_use_engine_serves_other_threads_without_complaint(tmp_path: Path) -> None:
    """Review: the shared per-thread pool printed 'can only be used in that same thread' errors."""
    import threading

    engine = open_engine(tmp_path / "t.db", new_database_key(), one_connection_per_use=True)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except BaseException as error:  # noqa: BLE001  (the test reports any failure)
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and engine.pool.status().startswith("NullPool")
