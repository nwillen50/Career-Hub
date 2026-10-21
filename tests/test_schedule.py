from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.schedule import (
    LABEL,
    install_plist,
    launchctl_commands,
    parse_clock,
    render_plist,
)


def test_the_plist_runs_hub_nightly_at_the_chosen_time(tmp_path: Path) -> None:
    data = plistlib.loads(render_plist("03:30", python="/venv/bin/python", logs=tmp_path))
    assert data["Label"] == LABEL
    assert data["ProgramArguments"] == ["/venv/bin/python", "-m", "careerhub.cli", "nightly"]
    assert data["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
    assert data["RunAtLoad"] is False
    assert data["StandardOutPath"] == str(tmp_path / "nightly.out.log")


def test_paths_with_special_characters_stay_valid_xml(tmp_path: Path) -> None:
    weird = tmp_path / "a <b> & c"
    data = plistlib.loads(render_plist("02:00", python="/p&q/python", logs=weird))
    assert data["ProgramArguments"][0] == "/p&q/python" and "a <b> & c" in data["StandardErrorPath"]


@pytest.mark.parametrize("bad", ["2:00", "24:00", "02:60", "noon", "", "02:00 "])
def test_bad_times_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_clock(bad)


def test_install_writes_the_file_but_does_not_load_it(tmp_path: Path) -> None:
    path = install_plist("02:00", folder=tmp_path / "LaunchAgents")
    assert path.name == f"{LABEL}.plist" and plistlib.loads(path.read_bytes())["Label"] == LABEL
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    path2 = install_plist("04:15", folder=tmp_path / "LaunchAgents")
    assert path2 == path
    assert plistlib.loads(path.read_bytes())["StartCalendarInterval"]["Hour"] == 4


def test_the_commands_are_text_for_the_user_to_run(tmp_path: Path) -> None:
    start, stop = launchctl_commands(tmp_path / "x.plist")
    assert start.startswith("launchctl bootstrap gui/") and "x.plist" in start
    assert stop.endswith(LABEL) and "bootout" in stop


def test_hub_schedule_install_uses_the_setting_and_prints_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text('{"nightly_run_time": "05:45"}')
    assert main(["schedule", "install"], InMemoryKeyStore()) == 0
    out = capsys.readouterr().out
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert "not running yet" in out and "launchctl bootstrap" in out and str(plist) in out
    assert plistlib.loads(plist.read_bytes())["StartCalendarInterval"] == {"Hour": 5, "Minute": 45}
