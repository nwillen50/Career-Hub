"""Run the nightly job only while plugged in (CH-163). Saved sample outputs, no real pmset."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from careerhub import cli
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings
from careerhub.power import parse_power_source, read_power_source
from careerhub.schedule import render_plist

AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining\n"
BATTERY = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t64%; discharging; 3:10\n"
UPS = "Now drawing from 'UPS Power'\n"
DESKTOP = "Now drawing from 'AC Power'\n"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (AC, "ac"),
        (BATTERY, "battery"),
        (UPS, "battery"),
        (DESKTOP, "ac"),
        ("", "unknown"),
        ("\n\n", "unknown"),
        ("pmset: something odd", "unknown"),
        ("No batteries available", "unknown"),
    ],
)
def test_reading_pmset_output(output: str, expected: str) -> None:
    assert parse_power_source(output) == expected


def test_off_a_mac_or_when_the_command_fails_the_source_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert read_power_source() == "unknown"
    monkeypatch.setattr("sys.platform", "darwin")

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("pmset")

    monkeypatch.setattr(subprocess, "run", missing)
    assert read_power_source() == "unknown"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=BATTERY, stderr=""),
    )
    assert read_power_source() == "battery"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="")
    )
    assert read_power_source() == "unknown"


def test_on_battery_the_run_is_skipped_quietly_and_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    ran: list[str] = []
    monkeypatch.setattr(cli, "run_nightly", lambda *a, **k: ran.append("ran"))
    assert cli._nightly(InMemoryKeyStore(), if_plugged_in=True, power=lambda: "battery") == 0
    assert "Skipped: the Mac is on battery power" in capsys.readouterr().out and ran == []


@pytest.mark.parametrize("source", ["ac", "unknown"])
def test_plugged_in_or_unreadable_power_runs_the_job(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    ran: list[str] = []

    class Report:
        problems: list[str] = []

        def summary(self) -> str:
            return "done"

    monkeypatch.setattr(cli, "run_nightly", lambda *a, **k: ran.append("ran") or Report())
    assert cli._nightly(InMemoryKeyStore(), if_plugged_in=True, power=lambda: source) == 0
    assert ran == ["ran"]


def test_without_the_flag_the_power_source_is_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))

    class Report:
        problems: list[str] = []

        def summary(self) -> str:
            return "done"

    monkeypatch.setattr(cli, "run_nightly", lambda *a, **k: Report())

    def boom() -> str:
        raise AssertionError("must not be asked")

    assert cli._nightly(InMemoryKeyStore(), if_plugged_in=False, power=boom) == 0


def test_the_job_file_passes_the_flag_only_when_the_setting_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = plistlib.loads(render_plist("02:00", "python", tmp_path, require_power=True))
    assert arguments["ProgramArguments"][-2:] == ["nightly", "--if-plugged-in"]
    plain = plistlib.loads(render_plist("02:00", "python", tmp_path))
    assert plain["ProgramArguments"][-1] == "nightly"
    assert Settings().nightly_require_power is True


def test_hub_nightly_arguments_are_checked() -> None:
    assert cli.main(["nightly", "--bogus"], InMemoryKeyStore()) == 2
