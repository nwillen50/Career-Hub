"""The user's settings, stored as ``settings.json`` in the data folder.

The file is optional: with no file every setting has a default. A file that exists but is
malformed or has an unknown or invalid value raises `SettingsError` naming the problem, rather
than quietly falling back to defaults and hiding a typo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from careerhub.core.paths import data_dir

#: File name of the settings file inside the data folder.
SETTINGS_FILENAME = "settings.json"

_CLOCK_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(Exception):
    """The settings file can't be read or holds an invalid value."""


class Settings(BaseModel):
    """Every user setting, with its default. Unknown names are rejected to catch typos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: How much detail goes in the log file.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    #: How many nightly backups to keep before the oldest is removed.
    backup_keep: int = Field(default=7, ge=1, le=365)
    #: Local time (24-hour ``HH:MM``) the nightly run starts.
    nightly_run_time: str = "02:00"

    @field_validator("nightly_run_time")
    @classmethod
    def _check_clock_time(cls, value: str) -> str:
        """Require a 24-hour ``HH:MM`` time."""
        if not _CLOCK_TIME.match(value):
            raise ValueError("must be a 24-hour time like 02:00")
        return value


def settings_path() -> Path:
    """Return where the settings file lives (it may not exist)."""
    return data_dir() / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from `path` (default: the data folder), or defaults if there is no file."""
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except json.JSONDecodeError as error:
        raise SettingsError(
            f"{path.name} is not valid JSON: {error.msg} (line {error.lineno})"
        ) from None
    except ValidationError as error:
        # Report setting names and rules only, never the values, in case a value is personal.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise SettingsError(f"{path.name} has invalid settings: {problems}") from None


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write settings to `path` (default: the data folder), readable only by the user."""
    path = path or settings_path()
    path.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path
