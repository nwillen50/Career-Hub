from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect

from careerhub.core import migrate as migrate_module
from careerhub.core.migrate import (
    MIGRATIONS_DIR,
    current_revision,
    head_revision,
    main,
    migrate,
    new_revision,
)
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return open_engine(tmp_path / "careerhub.db", new_database_key())


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    """A private copy of the migrations folder, so tests can add files to it."""
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def test_migrate_brings_a_new_database_to_the_latest_revision(engine: Engine) -> None:
    assert migrate(engine) == head_revision()
    assert current_revision(engine) == head_revision()


def test_migrate_is_safe_to_run_every_start(engine: Engine) -> None:
    first = migrate(engine)
    assert migrate(engine) == first


def test_new_revision_writes_a_file_and_migrate_applies_it(
    engine,
    scripts: Path,
) -> None:
    path = new_revision("example change", script_location=scripts)
    assert path is not None and path.exists()
    text = path.read_text()
    assert "example change" in text
    assert "def upgrade() -> None:" in text and "def downgrade() -> None:" in text
    new_head = head_revision(scripts)
    assert new_head is not None and new_head != head_revision()
    assert migrate(engine, script_location=scripts) == new_head
    assert "alembic_version" in inspect(engine).get_table_names()


def test_migrations_cannot_run_without_the_encrypted_connection(scripts: Path) -> None:
    from alembic import command

    config = migrate_module._config(scripts)
    with pytest.raises(RuntimeError, match="careerhub.core.migrate"):
        command.upgrade(config, "head")


def test_command_line_usage_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert main(["new", "  "]) == 2
    assert main(["other", "x"]) == 2
    assert "Usage" in capsys.readouterr().out


def test_generated_migrations_pass_lint_and_format(scripts: Path) -> None:
    """Every new migration must pass the same checks as CI, with no hand edits."""
    path = new_revision("example change", script_location=scripts)
    assert path is not None
    repo_config = Path(__file__).resolve().parents[2] / "pyproject.toml"
    ruff = migrate_module._ruff_executable()
    assert ruff is not None
    for args in (["check"], ["format", "--check"]):
        result = subprocess.run(
            [ruff, *args, "--config", str(repo_config), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
