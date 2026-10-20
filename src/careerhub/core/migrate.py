"""Brings a database's structure up to date, and writes new migration files.

`migrate(engine)` applies every migration the database hasn't had yet. It's safe to call every
time Career Hub starts: if the database is current, it does nothing.

`new_revision(message)` is for development. It writes a new migration file describing how the
models differ from the latest migration (see ``migrations/README.md``). Run it as::

    uv run python -m careerhub.core.migrate new "add job table"
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine

#: Folder holding env.py and the versions/ folder.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _config(script_location: Path | None = None) -> Config:
    """Return an Alembic config that needs no alembic.ini file."""
    config = Config()
    config.set_main_option("script_location", str(script_location or MIGRATIONS_DIR))
    _add_ruff_hooks(config)
    return config


def _ruff_executable() -> str | None:
    """Find Ruff: next to the running Python (the project's environment), else on PATH."""
    beside_python = Path(sys.executable).parent / "ruff"
    if beside_python.exists():
        return str(beside_python)
    return shutil.which("ruff")


def _add_ruff_hooks(config: Config) -> None:
    """Have Alembic fix and format every migration file it writes, so it passes CI as written."""
    ruff = _ruff_executable()
    if ruff is None:
        return
    config.set_section_option("post_write_hooks", "hooks", "ruff_fix ruff_format")
    for name, options in (
        ("ruff_fix", "check --fix --exit-zero REVISION_SCRIPT_FILENAME"),
        ("ruff_format", "format REVISION_SCRIPT_FILENAME"),
    ):
        config.set_section_option("post_write_hooks", f"{name}.type", "exec")
        config.set_section_option("post_write_hooks", f"{name}.executable", ruff)
        config.set_section_option("post_write_hooks", f"{name}.options", options)


def head_revision(script_location: Path | None = None) -> str | None:
    """Return the newest migration's id, or None if there are no migrations yet."""
    return ScriptDirectory.from_config(_config(script_location)).get_current_head()


def current_revision(engine: Engine) -> str | None:
    """Return the id of the last migration applied to this database, or None."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def migrate(engine: Engine, script_location: Path | None = None) -> str | None:
    """Apply all pending migrations and return the database's revision afterward."""
    config = _config(script_location)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return current_revision(engine)


def new_revision(message: str, script_location: Path | None = None) -> Path | None:
    """Write a migration for the difference between the models and the latest migration.

    Returns the new file's path, or None if Alembic wrote nothing.
    """
    config = _config(script_location)
    with tempfile.TemporaryDirectory() as folder:
        # A throwaway encrypted database, brought up to the latest migration, to compare against.
        engine = open_engine(Path(folder) / "scratch.db", new_database_key())
        try:
            migrate(engine, script_location)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                script = command.revision(config, message=message, autogenerate=True)
        finally:
            engine.dispose()
    if script is None or isinstance(script, list):
        return None
    return Path(script.path)


def main(argv: list[str]) -> int:
    """Command-line entry: ``python -m careerhub.core.migrate new "<message>"``."""
    if len(argv) != 2 or argv[0] != "new" or not argv[1].strip():
        print('Usage: python -m careerhub.core.migrate new "<what this migration does>"')
        return 2
    path = new_revision(argv[1].strip())
    print(f"Wrote {path}. Read it before committing." if path else "No migration written.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
