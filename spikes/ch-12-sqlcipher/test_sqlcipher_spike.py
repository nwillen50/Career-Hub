"""CH-12 spike: can Career Hub use an encrypted SQLite database with SQLAlchemy and Alembic?

Each test answers one question the decision record (ADR-0001) depends on.
Run with:  pytest -q test_sqlcipher_spike.py
"""

from __future__ import annotations

import secrets
import shutil
import sqlite3
from pathlib import Path

import pytest
import sqlcipher3
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine, String, create_engine, event, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

PLAINTEXT_HEADER = b"SQLite format 3\x00"


# --- The pattern under test -------------------------------------------------


def make_engine(path: Path, key_hex: str) -> Engine:
    """Open an encrypted database.

    The key is 32 random bytes (64 hex characters) kept in the Keychain. Passing it
    as a raw key (x'...') skips SQLCipher's password-stretching step, which is only
    needed for human passwords, and avoids putting the key in a URL.
    """

    def connect() -> sqlcipher3.Connection:
        conn = sqlcipher3.connect(str(path))
        conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
        return conn

    engine = create_engine("sqlite://", creator=connect, module=sqlcipher3.dbapi2)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA journal_mode = WAL")
        cur.close()

    return engine


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "job"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))


@pytest.fixture
def key() -> str:
    return secrets.token_hex(32)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "careerhub.db"


# --- Questions ---------------------------------------------------------------


def test_orm_round_trip(db_path: Path, key: str) -> None:
    """Q1: Does the SQLAlchemy ORM read and write through SQLCipher?"""
    engine = make_engine(db_path, key)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Job(title="Data Analyst, Acme"))
        s.commit()
    with Session(engine) as s:
        assert s.scalars(select(Job.title)).one() == "Data Analyst, Acme"


def test_file_is_encrypted_on_disk(db_path: Path, key: str) -> None:
    """Q2: Is the file unreadable as plain SQLite, with no plaintext leaking?"""
    engine = make_engine(db_path, key)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Job(title="UNIQUE-MARKER-12345"))
        s.commit()
    engine.dispose()
    raw = db_path.read_bytes()
    assert not raw.startswith(PLAINTEXT_HEADER)
    assert b"UNIQUE-MARKER-12345" not in raw
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(db_path).execute("SELECT count(*) FROM sqlite_master").fetchone()


def test_wrong_key_is_rejected(db_path: Path, key: str) -> None:
    """Q3: Does a wrong key fail loudly rather than return garbage?"""
    engine = make_engine(db_path, key)
    Base.metadata.create_all(engine)
    engine.dispose()
    wrong = make_engine(db_path, secrets.token_hex(32))
    with pytest.raises(Exception, match="file is not a database"):
        with wrong.connect() as c:
            c.execute(text("SELECT count(*) FROM sqlite_master")).scalar()


def test_alembic_upgrade_and_downgrade(db_path: Path, key: str, tmp_path: Path) -> None:
    """Q4: Do Alembic migrations run against the encrypted database, both directions?"""
    engine = make_engine(db_path, key)
    script_dir = tmp_path / "migrations"
    versions = script_dir / "versions"
    versions.mkdir(parents=True)
    (script_dir / "env.py").write_text(
        "from alembic import context\n"
        "conn = context.config.attributes['connection']\n"
        "context.configure(connection=conn, target_metadata=None, render_as_batch=True)\n"
        "with context.begin_transaction():\n"
        "    context.run_migrations()\n"
    )
    (script_dir / "script.py.mako").write_text("")
    (versions / "0001_create_company.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision = '0001'\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n"
        "def upgrade():\n"
        "    op.create_table('company', sa.Column('id', sa.Integer, primary_key=True),"
        " sa.Column('name', sa.String(200), nullable=False))\n"
        "def downgrade():\n    op.drop_table('company')\n"
    )
    (versions / "0002_add_domain.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision = '0002'\ndown_revision = '0001'\nbranch_labels = None\ndepends_on = None\n"
        "def upgrade():\n"
        "    with op.batch_alter_table('company') as b:\n"
        "        b.add_column(sa.Column('domain', sa.String(200)))\n"
        "def downgrade():\n"
        "    with op.batch_alter_table('company') as b:\n"
        "        b.drop_column('domain')\n"
    )
    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    cols = {c["name"] for c in inspect(engine).get_columns("company")}
    assert cols == {"id", "name", "domain"}
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001")
    cols = {c["name"] for c in inspect(engine).get_columns("company")}
    assert cols == {"id", "name"}
    # Batch mode (copy-and-swap) is what SQLite needs for column changes; it must
    # keep the copy encrypted too.
    engine.dispose()
    assert not db_path.read_bytes().startswith(PLAINTEXT_HEADER)


def test_key_rotation(db_path: Path, key: str) -> None:
    """Q5: Can the key be rotated in place (PRAGMA rekey)?"""
    engine = make_engine(db_path, key)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Job(title="kept across rekey"))
        s.commit()
    new_key = secrets.token_hex(32)
    with engine.connect() as c:
        c.exec_driver_sql(f"PRAGMA rekey = \"x'{new_key}'\"")
    engine.dispose()
    with Session(make_engine(db_path, new_key)) as s:
        assert s.scalars(select(Job.title)).one() == "kept across rekey"


def test_backup_stays_encrypted(db_path: Path, key: str, tmp_path: Path) -> None:
    """Q6: Which backup methods keep the copy encrypted?"""
    engine = make_engine(db_path, key)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Job(title="backup me"))
        s.commit()

    # Method A: VACUUM INTO, a consistent online snapshot.
    vacuum_copy = tmp_path / "vacuum.db"
    with engine.connect() as c:
        c.exec_driver_sql(f"VACUUM INTO '{vacuum_copy}'")
    engine.dispose()

    # Method B: plain file copy after checkpointing the WAL.
    file_copy = tmp_path / "copy.db"
    conn = make_engine(db_path, key).raw_connection()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    shutil.copy2(db_path, file_copy)

    results = {}
    for name, p in {"vacuum_into": vacuum_copy, "file_copy": file_copy}.items():
        encrypted = not p.read_bytes().startswith(PLAINTEXT_HEADER)
        readable = False
        try:
            with Session(make_engine(p, key)) as s:
                readable = s.scalars(select(Job.title)).one() == "backup me"
        except Exception:
            readable = False
        results[name] = (encrypted, readable)
    print("\nbackup results (encrypted, readable with key):", results)
    # The decision only needs one method that is both encrypted and restorable.
    assert any(enc and ok for enc, ok in results.values()), results


def test_batch_alter_without_alembic_runner(db_path: Path, key: str) -> None:
    """Q7: Does Alembic's Operations API (used by autogenerate review) work too?"""
    engine = make_engine(db_path, key)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        with ops.batch_alter_table("job") as b:
            b.add_column(__import__("sqlalchemy").Column("status", String(20)))
    assert "status" in {c["name"] for c in inspect(engine).get_columns("job")}
