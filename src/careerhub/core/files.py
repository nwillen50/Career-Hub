"""Writing files that hold personal data."""

from __future__ import annotations

import os
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path` so that only the user can ever read it.

    The file is created with mode 600 from the start (not written and then `chmod`ed), so there is
    no moment when another user could read it; an existing file is tightened too.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    except BaseException:
        # fdopen owns the descriptor once it succeeds; close it only if we never got that far.
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
