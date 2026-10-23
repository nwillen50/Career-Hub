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
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from careerhub.core.files import write_private
from careerhub.core.paths import data_dir

#: Host names that mean "this machine".
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: File name of the settings file inside the data folder.
SETTINGS_FILENAME = "settings.json"

_CLOCK_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(Exception):
    """The settings file can't be read or holds an invalid value."""


def check_https(address: str, allow_this_machine: bool) -> None:
    """Raise `ValueError` unless `address` is https with a host (or on this machine, if allowed)."""
    parts = urlsplit(address.strip())
    if not parts.hostname:
        raise ValueError("the address needs a host, like https://api.example.com/v1")
    if parts.scheme == "https":
        return
    if allow_this_machine and parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
        return
    raise ValueError("a model address must start with https:// (keys and prompts travel there)")


class ProviderSetting(BaseModel):
    """One model the router may use. API keys are never stored here (they live in the Keychain)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Unique name used in routes, e.g. "claude-cheap".
    name: str = Field(min_length=1)
    kind: Literal["anthropic", "openai", "google", "local"]
    model: str = Field(min_length=1)
    #: Override the API address (for local servers or compatible APIs).
    base_url: str | None = None
    #: True if the provider's terms let it train on API data; such providers never see
    #: personal data (PRD, privacy tiers). A cloud model with no value here is assumed to train
    #: (CH-117); only the user can say it doesn't. A local model keeps data on this Mac.
    trains_on_data: bool = True
    #: Dollars per million tokens, used to estimate cost. Leave unset for local models.
    input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _assume_cloud_models_train(cls, data: object) -> object:
        """Treat a missing `trains_on_data` as true for cloud kinds and false for `local`."""
        if isinstance(data, dict) and "trains_on_data" not in data:
            return {**data, "trains_on_data": data.get("kind") != "local"}
        return data

    @model_validator(mode="after")
    def _cloud_address_is_https(self) -> ProviderSetting:
        """Require a cloud model's address to be https with a host (keys travel there)."""
        if self.kind != "local" and self.base_url is not None:
            check_https(self.base_url, allow_this_machine=False)
        return self


class PresetOverride(BaseModel):
    """Changes to a catalog preset. Only these fields can change; privacy fields can't."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str | None = Field(default=None, min_length=1)
    base_url: str | None = None
    input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float | None = Field(default=None, ge=0)

    @field_validator("base_url")
    @classmethod
    def _address_is_safe(cls, value: str | None) -> str | None:
        """Https with a host; plain http only for an address on this machine (local presets)."""
        if value is not None:
            check_https(value, allow_this_machine=True)
        return value


class Settings(BaseModel):
    """Every user setting, with its default. Unknown names are rejected to catch typos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: How much detail goes in the log file.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    #: How many nightly backups to keep before the oldest is removed.
    backup_keep: int = Field(default=7, ge=1, le=365)
    #: Most the model router may spend on cloud models in a calendar month, in US dollars. When
    #: it is reached, calls not marked essential are paused (PRD, "Cost tracking").
    monthly_budget_usd: float = Field(default=20.0, ge=0)
    #: A board that lists no jobs for this many nights in a row (and whose jobs could not be
    #: checked one by one) is treated as having closed everything (CH-103).
    empty_listing_backstop_nights: int = Field(default=3, ge=1, le=30)
    #: A profile list item (a skill, a school, a location) that changes by more than this many
    #: letters counts as a new item and re-scores jobs; a smaller change is a typo fix (CH-115).
    score_typo_distance: int = Field(default=2, ge=0, le=10)
    #: Re-score once at least this percent of the words in About or the accepted resume facts have
    #: changed since the last scoring run (CH-115). Small edits add up.
    score_free_text_change_percent: float = Field(default=5.0, ge=0, le=100)
    #: If a board's listing loses more than this share of its open jobs in one night, it may be cut
    #: off, so the missing jobs are checked (or kept open for a while) rather than closed (CH-132).
    listing_drop_share: float = Field(default=0.5, gt=0, le=1)
    #: Days a saved model answer is kept after it was last used (using it resets the clock).
    cache_ttl_days: int = Field(default=30, ge=1, le=3650)
    #: Skills the profile needs before scoring can be switched on (CH-102).
    profile_min_skills: int = Field(default=5, ge=0, le=100)
    #: A posting older than this many days is ranked slightly lower (never hidden) (CH-81).
    stale_after_days: int = Field(default=21, ge=1, le=365)
    #: How many fit-score points (of 100) an old posting loses in the ordering. 0 turns it off.
    stale_penalty_points: float = Field(default=5.0, ge=0, le=50)
    #: Your school's LinkedIn page name (the last part of linkedin.com/school/<name>), so the
    #: shortlist can offer a link to its alumni at each company. Career Hub only builds the link.
    linkedin_school_slug: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]+$")
    #: Scores at or above this get the green bubble; at or above `score_tier_mid`, the brass one.
    score_tier_high: int = Field(default=75, ge=1, le=100)
    score_tier_mid: int = Field(default=55, ge=0, le=99)
    #: Your timezone (an IANA name such as "America/New_York"), for working hours and calendars.
    timezone: str = "America/New_York"
    #: Calendar files (.ics) whose busy time the weekly plan avoids. Only times are read.
    calendar_files: tuple[str, ...] = ()
    #: Read busy time from the Mac's Calendar app (needs your permission; Mac only).
    use_apple_calendar: bool = False
    #: Which Calendar-app calendars count, by name. Empty means all of them.
    apple_calendars: tuple[str, ...] = ()
    #: The models Career Hub may use. Empty until the user adds some.
    providers: tuple[ProviderSetting, ...] = ()
    #: Changes to built-in catalog presets by preset name, e.g. a newer model id or new prices.
    preset_overrides: dict[str, PresetOverride] = Field(default_factory=dict)
    #: Which providers handle each task, in order: the first is tried, later ones are escalations.
    #: Example: {"extract": ["claude-cheap", "claude-strong"]}.
    model_routes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: Local time (24-hour ``HH:MM``) the nightly run starts.
    nightly_run_time: str = "02:00"

    @model_validator(mode="after")
    def _check_providers_and_routes(self) -> Settings:
        """Require unique provider names and routes that name only known providers."""
        names = [provider.name for provider in self.providers]
        if len(set(names)) != len(names):
            raise ValueError("provider names must be unique")
        for task, route in self.model_routes.items():
            if not route:
                raise ValueError(f"route {task!r} names no providers")
            from careerhub.llm.catalog import preset_names  # lazy: llm imports this module

            unknown = [name for name in route if name not in names and name not in preset_names()]
            if unknown:
                raise ValueError(f"route {task!r} names unknown providers: {', '.join(unknown)}")
        return self

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        """Require a timezone name the system knows."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("must be an IANA timezone name like America/New_York") from None
        return value

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
    write_private(path, settings.model_dump_json(indent=2) + "\n")
    return path
