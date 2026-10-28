"""The built-in model catalog: presets the user switches on by adding a key.

`catalog.json` lists ready-made models (kind, model id, prices, whether the provider trains on API
data). A preset is **active** when its API key is in the Keychain (the local one: when the Ollama
server answers). `with_catalog` returns the user's settings with the active presets added as
providers and sensible default routes filled in for tasks the user hasn't routed, so adding a key
is enough to start using a model. The user's own `providers` and `model_routes` always win, and
`preset_overrides` in settings can change a preset's model, prices, or address.

Prices and model ids go stale: they come from public price pages on the date in `prices_checked`
(`stale_warning` complains after 90 days). Gemini's free tier trains on data, so it is only ever
used for Public tasks; the privacy check in the router enforces that, and so do the default routes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from careerhub.core.secrets import KeyStore
from careerhub.core.settings import ProviderSetting, Settings, SettingsError
from careerhub.llm.providers import api_key_name

#: The privacy tier of each task, which decides the providers it may use by default.
PERSONAL_TASKS = ("score", "tailor", "profile_extract", "chat")
PUBLIC_TASKS = ("extract",)
STALE_AFTER_DAYS = 90
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


class Preset(BaseModel):
    """One ready-made model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    kind: Literal["anthropic", "openai", "google", "local"]
    model: str = Field(min_length=1)
    #: Only Gemini has tiers: "free" trains on data, "paid" does not.
    tier: Literal["free", "paid"] | None = None
    strength: Literal["cheap", "strong"]
    input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float | None = Field(default=None, ge=0)
    trains_on_data: bool
    requests_per_minute: int | None = Field(default=None, ge=1)
    requests_per_day: int | None = Field(default=None, ge=1)


class Catalog(BaseModel):
    """The contents of `catalog.json`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prices_checked: date
    presets: tuple[Preset, ...]


def load_catalog() -> Catalog:
    """Read and validate the packaged catalog."""
    text = resources.files("careerhub.llm").joinpath("catalog.json").read_text(encoding="utf-8")
    return Catalog.model_validate(json.loads(text))


def preset_names() -> frozenset[str]:
    """Return every preset name (settings accept these in routes without a `providers` entry)."""
    return frozenset(p.name for p in load_catalog().presets)


def stale_warning(today: date) -> str | None:
    """Return a warning if the catalog's prices are older than 90 days, else None."""
    checked = load_catalog().prices_checked
    if (today - checked).days > STALE_AFTER_DAYS:
        return f"Model prices were last checked on {checked}; they may be out of date."
    return None


def ollama_is_running(client: httpx.Client | None = None) -> bool:
    """Return True if a local Ollama server answers with a model list (nothing is sent to it)."""
    try:
        response = (client or httpx).get(OLLAMA_TAGS_URL, timeout=1.0)
        return response.status_code == 200 and "models" in response.json()
    except (httpx.HTTPError, ValueError):
        return False


@dataclass(frozen=True)
class CatalogSetup:
    """Settings with active presets and default routes added, and the reason for each route."""

    settings: Settings
    active: tuple[str, ...]
    reasons: dict[str, str]


def _apply_overrides(preset: Preset, settings: Settings) -> ProviderSetting:
    """Turn a preset into a provider setting, with the user's overrides applied."""
    override = settings.preset_overrides.get(preset.name)
    values = {
        "name": preset.name,
        "kind": preset.kind,
        "model": preset.model,
        "trains_on_data": preset.trains_on_data,
        "input_per_mtok": preset.input_per_mtok,
        "output_per_mtok": preset.output_per_mtok,
        "requests_per_minute": preset.requests_per_minute,
        "requests_per_day": preset.requests_per_day,
    }
    if override is not None:
        values.update(override.model_dump(exclude_none=True))
    try:
        return ProviderSetting.model_validate(values)
    except (
        ValidationError
    ) as error:  # for example http:// for a cloud preset (the check needs the kind)
        reasons = "; ".join(str(e.get("ctx", {}).get("error") or e["msg"]) for e in error.errors())
        raise SettingsError(f"preset_overrides for {preset.name!r}: {reasons}") from None


def _cost(provider: ProviderSetting) -> float:
    """Rank by input plus output price (unpriced providers sort last)."""
    if provider.input_per_mtok is None or provider.output_per_mtok is None:
        return float("inf")
    return provider.input_per_mtok + provider.output_per_mtok


def _default_route(task: str, providers: list[ProviderSetting]) -> tuple[list[str], str]:
    """Return the provider names for `task` (cheapest first, local last) and the reason."""
    personal = task in PERSONAL_TASKS
    cloud = [p for p in providers if p.kind != "local" and not (personal and p.trains_on_data)]
    names = [p.name for p in sorted(cloud, key=_cost)]
    names += [p.name for p in providers if p.kind == "local"]
    if not names:
        why = "no model that may see personal data is active" if personal else "no model is active"
        return [], why
    rule = (
        "personal data: cheapest providers that don't train on it, then stronger, local last"
        if personal
        else "public data: cheapest provider first, local last"
    )
    return names, rule


def with_catalog(
    settings: Settings, store: KeyStore, local_running: Callable[[], bool] = ollama_is_running
) -> CatalogSetup:
    """Add the active presets to `settings` and fill in routes the user hasn't set.

    Default routes are built from the active presets only. Providers the user configured
    themselves are used only where their own `model_routes` say so.
    """
    taken = {p.name for p in settings.providers}
    added: list[ProviderSetting] = []
    for preset in load_catalog().presets:
        if preset.name in taken:
            continue  # the user's own entry with this name wins
        active = (
            local_running()
            if preset.kind == "local"
            else bool(store.get(api_key_name(preset.name)))
        )
        if active:
            added.append(_apply_overrides(preset, settings))
    providers = [*settings.providers, *added]
    routes = dict(settings.model_routes)
    reasons: dict[str, str] = {task: "set in settings" for task in routes}
    for task in (*PUBLIC_TASKS, *PERSONAL_TASKS):
        if task in routes:
            continue
        names, reason = _default_route(task, added)
        reasons[task] = reason
        if names:
            routes[task] = tuple(names)
    merged = settings.model_copy(update={"providers": tuple(providers), "model_routes": routes})
    return CatalogSetup(merged, tuple(p.name for p in added), reasons)
