"""The menu-bar helper's logic (CH-191), tested without a Mac or the rumps library."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from careerhub import cli, menubar
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.menubar import (
    UNKNOWN,
    WebLauncher,
    find_link,
    hub_command,
    parse_attention,
    read_state,
    web_is_running,
)
from careerhub.modules.attention import AttentionState
from careerhub.schedule import MENUBAR_LABEL, render_plist

LINK = "http://127.0.0.1:8765/?t=abc_DEF-123"


def test_the_icon_text_is_what_hub_attention_prints() -> None:
    state = AttentionState(follow_ups_due=2, new_top_jobs=3, error=False)
    shown = parse_attention(json.dumps(state.as_json()))
    assert (shown.title, shown.error) == ("5", False) and "2 follow-ups due" in shown.tooltip
    failed = parse_attention(json.dumps(AttentionState(0, 1, True).as_json()))
    assert failed.title == "! 1" and failed.error and "last nightly run failed" in failed.tooltip


@pytest.mark.parametrize("text", ["", "not json", "{}", '{"label": 1}', "[1, 2]", "null"])
def test_unreadable_output_shows_a_warning_not_a_stale_count(text: str) -> None:
    assert parse_attention(text) == UNKNOWN and UNKNOWN.error


def test_a_hostile_label_is_cut_short() -> None:
    state = parse_attention(json.dumps({"label": "x" * 500, "description": "y" * 900, "error": 0}))
    assert len(state.title) <= 12 and len(state.tooltip) <= 200


def test_read_state_runs_hub_attention_with_this_python_and_survives_failures() -> None:
    seen: list[list[str]] = []

    def good(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        payload = json.dumps(AttentionState(1, 0, False).as_json())
        return subprocess.CompletedProcess(command, 0, payload, "")

    assert read_state(good).title == "1"
    assert seen == [[sys.executable, "-m", "careerhub.cli", "attention", "--json"]]

    def broken(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "boom")

    assert read_state(broken) == UNKNOWN

    def missing(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    assert read_state(missing) == UNKNOWN


def test_the_link_is_found_only_when_it_is_the_local_one() -> None:
    assert find_link(f"Career Hub is at {LINK}\n") == LINK
    assert find_link("Career Hub is at https://evil.example/?t=abc") is None
    assert find_link("Career Hub is at http://127.0.0.1.evil.example:8765/?t=abc") is None
    assert find_link("") is None


class FakeProcess:
    def __init__(self, line: str) -> None:
        import io

        self.stdout = io.StringIO(line)


def test_open_today_starts_the_server_once_and_opens_the_link() -> None:
    spawned: list[list[str]] = []
    opened: list[str] = []
    listening = {"yes": False}

    def spawn(command: list[str], **_: Any) -> FakeProcess:
        spawned.append(command)
        listening["yes"] = True
        return FakeProcess(f"Career Hub is at {LINK}\nOpen that link\n")

    launcher = WebLauncher(
        8765, spawn, lambda url: opened.append(url) or True, lambda port: listening["yes"]
    )  # type: ignore[arg-type]
    assert launcher.open_today() == "Opened Today."
    assert launcher.open_today() == "Opened Today."  # the second click reuses the server
    assert len(spawned) == 1 and opened == [LINK, LINK]
    assert spawned[0] == [sys.executable, "-m", "careerhub.cli", "web", "--port", "8765"]


def test_open_today_does_not_start_a_second_server_or_guess_a_secret() -> None:
    spawned: list[Any] = []
    launcher = WebLauncher(
        8765, lambda *a, **k: spawned.append(1), lambda url: True, lambda port: True
    )  # type: ignore[arg-type,return-value]
    assert "already running" in launcher.open_today() and spawned == []


def test_open_today_says_so_when_the_server_does_not_start() -> None:
    launcher = WebLauncher(
        8765, lambda *a, **k: FakeProcess("Traceback ...\n"), lambda url: True, lambda port: False
    )  # type: ignore[arg-type,return-value]
    assert "could not start" in launcher.open_today()


def test_web_is_running_reads_a_connection_attempt() -> None:
    class Sock:
        def close(self) -> None:
            pass

    assert web_is_running(1, lambda *a, **k: Sock()) is True  # type: ignore[arg-type,return-value]

    def refuse(*a: Any, **k: Any) -> None:
        raise ConnectionRefusedError

    assert web_is_running(1, refuse) is False  # type: ignore[arg-type]


def test_the_login_agent_starts_at_login_and_restarts_only_after_a_crash(tmp_path: Path) -> None:
    data = plistlib.loads(
        render_plist("00:00", python="/venv/bin/python", logs=tmp_path, command="menubar")
    )
    assert data["Label"] == MENUBAR_LABEL == "com.careerhub.menubar"
    assert data["ProgramArguments"] == ["/venv/bin/python", "-m", "careerhub.cli", "menubar"]
    assert data["RunAtLoad"] is True and data["KeepAlive"] == {"SuccessfulExit": False}
    assert "StartCalendarInterval" not in data and "StartInterval" not in data


def test_schedule_install_writes_the_agent_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    data = tmp_path / "data"
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(data))
    agents = tmp_path / "Library" / "LaunchAgents"
    assert cli.main(["schedule", "install"], InMemoryKeyStore()) == 0
    assert not (agents / "com.careerhub.menubar.plist").exists()
    (data / "settings.json").write_text('{"menubar_on": true}', encoding="utf-8")
    assert cli.main(["schedule", "install"], InMemoryKeyStore()) == 0
    assert (agents / "com.careerhub.menubar.plist").exists()
    assert "not running yet" in capsys.readouterr().out


def test_the_command_says_what_is_missing_off_a_mac_or_without_the_library(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(menubar.sys, "platform", "linux")
    assert cli.main(["menubar"], InMemoryKeyStore()) == 1
    assert "only on a Mac" in capsys.readouterr().out
    monkeypatch.setattr(menubar.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "rumps", None)  # import fails as if not installed
    assert cli.main(["menubar"], InMemoryKeyStore()) == 1
    assert "uv sync --extra menubar" in capsys.readouterr().out


def test_hub_command_uses_this_python() -> None:
    assert hub_command("web") == [sys.executable, "-m", "careerhub.cli", "web"]
