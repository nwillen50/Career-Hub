from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from careerhub.core import paths


@pytest.fixture(autouse=True)
def no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)


def test_override_is_used_and_created_privately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nested" / "careerhub-data"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(target))
    assert paths.data_dir() == target
    assert target.is_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_existing_folder_is_made_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "data"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(target))
    paths.data_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_create_false_does_not_touch_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "not-yet"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(target))
    assert paths.data_dir(create=False) == target
    assert not target.exists()


def test_database_lives_in_the_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path))
    assert paths.database_path() == tmp_path / "careerhub.db"


def test_default_on_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.default_data_dir() == tmp_path / "Library" / "Application Support" / "CareerHub"


def test_default_on_linux_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert paths.default_data_dir() == tmp_path / ".local" / "share" / "careerhub"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.default_data_dir() == tmp_path / "xdg" / "careerhub"
