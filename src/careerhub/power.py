"""Is the Mac plugged in? (CH-163).

The PRD says the full overnight run happens only when plugged in.
launchd has no power condition, so `hub nightly --if-plugged-in` asks macOS itself with
`pmset -g batt`. The text parsing is a pure function, tested with saved sample outputs. If the
power source can't be read, the job runs anyway: a check that fails must never mean "never run".
"""

from __future__ import annotations

import subprocess
import sys
from typing import Literal

PowerSource = Literal["ac", "battery", "unknown"]


def parse_power_source(output: str) -> PowerSource:
    """Read the first line of `pmset -g batt`, such as "Now drawing from 'AC Power'"."""
    first = output.strip().splitlines()[0].lower() if output.strip() else ""
    if "ac power" in first:
        return "ac"
    if "battery power" in first or "ups power" in first:
        return "battery"
    return "unknown"


def read_power_source() -> PowerSource:
    """Ask macOS where power comes from. Off a Mac, or if the command fails: "unknown"."""
    if sys.platform != "darwin":
        return "unknown"
    try:
        done = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return parse_power_source(done.stdout) if done.returncode == 0 else "unknown"
