"""Checks on the repository itself, not on the application.

Bug CH-39: a too-broad ignore pattern ("secrets.*") silently kept src/careerhub/core/secrets.py out
of git. These tests make sure no source or test file can be ignored like that again.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="needs git and a git checkout",
)


def _ignored(paths: list[str]) -> list[str]:
    """Return the subset of `paths` (relative to the repo root) that git would ignore."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=REPO_ROOT,
        input="\n".join(paths),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.split()


def test_no_python_source_or_test_file_is_ignored() -> None:
    files = [
        str(p.relative_to(REPO_ROOT))
        for folder in ("src", "tests", "spikes")
        for p in (REPO_ROOT / folder).rglob("*.py")
    ]
    assert files, "expected to find Python files"
    assert _ignored(files) == []


@pytest.mark.parametrize(
    "path",
    [
        "src/careerhub/core/secrets.py",
        "src/careerhub/llm/secrets_policy.py",
        "tests/core/test_secrets.py",
    ],
)
def test_files_named_like_secrets_inside_the_code_are_not_ignored(path: str) -> None:
    assert _ignored([path]) == []


@pytest.mark.parametrize(
    "path",
    [
        "secrets.json",
        "secrets.yaml",
        ".env",
        ".env.local",
        "careerhub.db",
        "careerhub.db-wal",
        "data/profile.md",
        "connections.csv",
        "transcript.pdf",
        "api.secret",
    ],
)
def test_personal_data_and_secret_files_are_ignored(path: str) -> None:
    assert _ignored([path]) == [path]
