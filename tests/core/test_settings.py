from __future__ import annotations

import stat
from pathlib import Path

import pytest

from careerhub.core.settings import (
    SETTINGS_FILENAME,
    Settings,
    SettingsError,
    load_settings,
    save_settings,
    settings_path,
)


def test_no_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / SETTINGS_FILENAME) == Settings()


def test_saved_settings_load_back_and_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / SETTINGS_FILENAME
    save_settings(Settings(log_level="DEBUG", backup_keep=3, nightly_run_time="23:30"), path)
    assert load_settings(path) == Settings(
        log_level="DEBUG", backup_keep=3, nightly_run_time="23:30"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_partial_file_fills_in_defaults(tmp_path: Path) -> None:
    path = tmp_path / SETTINGS_FILENAME
    path.write_text('{"backup_keep": 2}')
    assert load_settings(path) == Settings(backup_keep=2)


@pytest.mark.parametrize(
    "text, message",
    [
        ("{not json", "not valid JSON"),
        ('{"backup_kep": 2}', "backup_kep"),
        ('{"backup_keep": 0}', "backup_keep"),
        ('{"log_level": "LOUD"}', "log_level"),
        ('{"nightly_run_time": "25:00"}', "nightly_run_time"),
        ('{"nightly_run_time": "2am"}', "nightly_run_time"),
    ],
)
def test_bad_files_raise_a_clear_error(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / SETTINGS_FILENAME
    path.write_text(text)
    with pytest.raises(SettingsError, match=message):
        load_settings(path)


def test_error_messages_never_echo_values(tmp_path: Path) -> None:
    path = tmp_path / SETTINGS_FILENAME
    path.write_text('{"nightly_run_time": "jane.doe@example.com"}')
    with pytest.raises(SettingsError) as raised:
        load_settings(path)
    assert "jane.doe" not in str(raised.value)


def test_default_location_is_the_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert settings_path() == tmp_path / SETTINGS_FILENAME
