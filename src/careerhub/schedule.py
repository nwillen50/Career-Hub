"""Runs the nightly pipeline on a schedule using launchd, macOS's own scheduler (step 6).

`render_plist` builds the launchd job as a property list; `install_plist` writes it to
`~/Library/LaunchAgents`. Career Hub **writes the file but does not load it**: starting a
recurring job on the user's Mac is the user's call, so `hub schedule install` prints the
`launchctl` command to run. Everything here is plain text and file writing, so it is tested on any
system; loading and running the job needs a Mac (CH-71 notes).

launchd runs a job that was due while the Mac slept as soon as it wakes, so a closed laptop at
the scheduled time only delays the run.
"""

from __future__ import annotations

import os
import plistlib
import re
import sys
from pathlib import Path

from careerhub.core.paths import data_dir

LABEL = "com.careerhub.nightly"
REFRESH_LABEL = "com.careerhub.refresh"
CATCHUP_LABEL = "com.careerhub.catchup"
MENUBAR_LABEL = "com.careerhub.menubar"
#: How often the catch-up job looks (seconds). It does nothing unless a night was skipped.
CATCHUP_INTERVAL_SECONDS = 1800
_JOBS = {
    "nightly": LABEL,
    "refresh": REFRESH_LABEL,
    "catchup": CATCHUP_LABEL,
    "menubar": MENUBAR_LABEL,
}
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_clock(value: str) -> tuple[int, int]:
    """Turn "02:00" into (2, 0). Raises ValueError for anything but a 24-hour HH:MM time."""
    match = _CLOCK.match(value)
    if match is None:
        raise ValueError("The run time must be a 24-hour time like 02:00.")
    return int(match.group(1)), int(match.group(2))


def logs_dir() -> Path:
    """Return the folder for the job's output, inside the private data folder."""
    folder = data_dir() / "logs"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def render_plist(
    run_time: str,
    python: str | None = None,
    logs: Path | None = None,
    command: str = "nightly",
    require_power: bool = False,
) -> bytes:
    """Build the launchd property list that runs `hub <command>` every day at `run_time`.

    `command` is `nightly` (label `com.careerhub.nightly`) or `refresh` (the midday job). With
    `require_power` the nightly job passes `--if-plugged-in`, so on battery it skips that night.
    """
    if command not in _JOBS:
        raise ValueError("command must be 'nightly', 'refresh', 'catchup', or 'menubar'")
    if require_power and command != "nightly":
        raise ValueError("only the nightly job can require power")
    hour, minute = parse_clock(run_time) if command in ("nightly", "refresh") else (0, 0)
    logs = logs or logs_dir()
    if command == "menubar":  # the login agent: starts at login, restarts only after a crash
        return plistlib.dumps(
            {
                "Label": MENUBAR_LABEL,
                "ProgramArguments": [python or sys.executable, "-m", "careerhub.cli", "menubar"],
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "StandardOutPath": str(logs / "menubar.out.log"),
                "StandardErrorPath": str(logs / "menubar.err.log"),
                "ProcessType": "Interactive",
            }
        )
    job = {
        "Label": _JOBS[command],
        # The venv's own Python, so the job doesn't depend on the shell's PATH.
        "ProgramArguments": [
            python or sys.executable,
            "-m",
            "careerhub.cli",
            *(["nightly", "--catch-up"] if command == "catchup" else [command]),
            *(["--if-plugged-in"] if require_power else []),
        ],
        # The catch-up job looks every 30 minutes; the others run once a day.
        **(
            {"StartInterval": CATCHUP_INTERVAL_SECONDS}
            if command == "catchup"
            else {"StartCalendarInterval": {"Hour": hour, "Minute": minute}}
        ),
        "RunAtLoad": False,
        "StandardOutPath": str(logs / f"{command}.out.log"),
        "StandardErrorPath": str(logs / f"{command}.err.log"),
        # Be a polite background job.
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    }
    return plistlib.dumps(job)


def agents_dir() -> Path:
    """Return the user's launch agents folder (macOS)."""
    return Path.home() / "Library" / "LaunchAgents"


def install_plist(
    run_time: str,
    folder: Path | None = None,
    command: str = "nightly",
    require_power: bool = False,
) -> Path:
    """Write the plist for `command` (replacing an older one for this job) and return its path.

    It is *not* loaded; the caller shows the user the `launchctl` command.
    """
    folder = folder or agents_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_JOBS[command]}.plist"
    path.write_bytes(render_plist(run_time, command=command, require_power=require_power))
    os.chmod(path, 0o644)  # launchd requires agents not to be writable by others
    return path


def launchctl_commands(path: Path, label: str = LABEL) -> tuple[str, str]:
    """Return the commands to start and to stop the job, for the user to run."""
    domain = f"gui/{os.getuid()}"
    return (
        f"launchctl bootstrap {domain} {path}",
        f"launchctl bootout {domain}/{label}",
    )
