"""Where Career Hub keeps the user's data on disk.

Everything personal (the database, backups, profile files, exports) lives in one data folder,
never in the repository:

- macOS: ``~/Library/Application Support/CareerHub``
- Linux and others (development and tests): ``$XDG_DATA_HOME/careerhub``, which defaults to
  ``~/.local/share/careerhub``

Set the ``CAREERHUB_DATA_DIR`` environment variable to use a different folder, for example a
temporary one in tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Environment variable that overrides the data folder.
DATA_DIR_ENV = "CAREERHUB_DATA_DIR"

#: File name of the encrypted database inside the data folder.
DATABASE_FILENAME = "careerhub.db"

# Only the user may read, write, or list the data folder (rwx------).
_PRIVATE_DIR_MODE = 0o700


def default_data_dir() -> Path:
    """Return the standard data folder for this operating system, ignoring any override."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "CareerHub"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / "careerhub"


def data_dir(create: bool = True) -> Path:
    """Return the data folder, honoring ``CAREERHUB_DATA_DIR``.

    With ``create=True`` (the default) the folder is created if needed, readable only by the
    current user.
    """
    override = os.environ.get(DATA_DIR_ENV)
    folder = Path(override).expanduser() if override else default_data_dir()
    if create:
        folder.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        # mkdir's mode is reduced by the umask and isn't applied to an existing folder,
        # so set it explicitly.
        folder.chmod(_PRIVATE_DIR_MODE)
    return folder


def database_path(create_dir: bool = True) -> Path:
    """Return the path of the encrypted database file inside the data folder."""
    return data_dir(create=create_dir) / DATABASE_FILENAME
