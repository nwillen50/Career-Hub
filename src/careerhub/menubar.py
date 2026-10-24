"""The menu-bar helper (CH-191): an icon with the count, and one item, "Open Today".

It runs on the user's Mac as a small background program (`hub menubar`, started at login by a
launchd agent that `hub schedule install` writes only when `menubar_on` is set). It never opens
the database itself: every minute it runs `hub attention --json` (counts and a yes/no only: no
company, title, or person) and shows the label and tooltip. "Open Today" starts `hub web` if it is
not running, reads the one-time link it prints, and opens that link in the browser.

The icon itself needs the optional `rumps` library (`uv sync --extra menubar`, macOS only).
Everything else here is plain code with injectable parts, tested off a Mac; the real icon is
checked in a Mac session (docs/mac-session.md). The choice of rumps over a Swift app is ADR 0048.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO

POLL_SECONDS = 60
LINK_WAIT_SECONDS = 20.0
ERROR_TITLE = "!"
_LINK = re.compile(r"Career Hub is at (http://127\.0\.0\.1:\d+/\?t=[A-Za-z0-9_\-]+)")


@dataclass(frozen=True)
class IconState:
    """What the icon shows: its text, its tooltip, and whether something is wrong."""

    title: str
    tooltip: str
    error: bool = False


#: Shown when `hub attention` cannot be read; the user sees something is off, not a stale count.
UNKNOWN = IconState(ERROR_TITLE, "Career Hub could not read its status", error=True)


def parse_attention(output: str) -> IconState:
    """Turn the text `hub attention --json` printed into the icon's state (UNKNOWN if bad)."""
    try:
        data = json.loads(output)
        title, tooltip = str(data["label"]), str(data["description"])
        error = bool(data["error"])
    except (ValueError, KeyError, TypeError):
        return UNKNOWN
    return IconState(title[:12], tooltip[:200], error)


def hub_command(*words: str) -> list[str]:
    """Return the command that runs `hub <words>` with this Python (not whatever is on PATH)."""
    return [sys.executable, "-m", "careerhub.cli", *words]


def read_state(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> IconState:
    """Ask `hub attention --json` for the current state."""
    try:
        done = run(hub_command("attention", "--json"), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    return parse_attention(done.stdout) if done.returncode == 0 else UNKNOWN


def find_link(output: str) -> str | None:
    """Return the one-time link in what `hub web` printed, or None."""
    found = _LINK.search(output)
    return found.group(1) if found else None


def web_is_running(
    port: int, connect: Callable[..., socket.socket] = socket.create_connection
) -> bool:
    """Say whether something already listens on this port on 127.0.0.1."""
    try:
        connect(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return False
    return True


class WebLauncher:
    """Starts `hub web` once and opens Today in the browser (the helper's one action)."""

    def __init__(
        self,
        port: int = 8765,
        spawn: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        opener: Callable[[str], bool] = webbrowser.open,
        running: Callable[[int], bool] = web_is_running,
    ) -> None:
        """Keep the pieces to start the server and open a link (replaced in tests)."""
        self.port, self._spawn, self._open, self._running = port, spawn, opener, running
        self._link: str | None = None

    def open_today(self) -> str:
        """Open Today; return a short sentence saying what happened (for a notification)."""
        if self._link is not None and self._running(self.port):
            self._open(self._link)
            return "Opened Today."
        if self._running(self.port):
            # Started by a terminal: its link carries a secret that only that window printed.
            return "Career Hub is already running: use the link in its terminal window."
        process = self._spawn(
            hub_command("web", "--port", str(self.port)),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            # Without this, the link sits in a buffer and is never read from the pipe.
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )  # fmt: skip
        link = find_link(_first_line(process.stdout))
        if link is None:
            return "Career Hub could not start its web page."
        self._link = link
        self._open(link)
        return "Opened Today."


def _first_line(stream: IO[str] | None, wait: float = LINK_WAIT_SECONDS) -> str:
    """Read one line from `stream`, giving up after `wait` seconds (never hangs the menu bar)."""
    if stream is None:
        return ""
    box: list[str] = []
    reader = threading.Thread(target=lambda: box.append(stream.readline()), daemon=True)
    reader.start()
    reader.join(wait)
    return box[0] if box else ""


def run_icon() -> int:
    """Show the icon until the user quits. Returns 1 with a message when it cannot run."""
    if sys.platform != "darwin":
        print("The menu-bar icon runs only on a Mac.")
        return 1
    try:
        import rumps
    except ImportError:
        print("The menu-bar icon needs one more library. Run: uv sync --extra menubar")
        return 1
    launcher = WebLauncher()

    class App(rumps.App):  # type: ignore[misc]  # rumps has no type information
        def __init__(self) -> None:
            super().__init__("Career Hub", title="…", quit_button="Quit")
            # A callback, not @rumps.clicked: that decorator is untyped, which strict mypy refuses
            # on a Mac where rumps is installed (CH-205).
            self.menu = [rumps.MenuItem("Open Today", callback=self.open_today)]
            self.timer = rumps.Timer(self.refresh, POLL_SECONDS)
            self.timer.start()
            self.refresh(None)

        def refresh(self, _: object) -> None:
            state = read_state()
            self.title = state.title
            self.tooltip = state.tooltip  # also says it in words: the "!" is never colour alone

        def open_today(self, _: object) -> None:
            message = launcher.open_today()
            if message != "Opened Today.":  # never a silent click
                rumps.notification("Career Hub", "", message)

    App().run()
    return 0
