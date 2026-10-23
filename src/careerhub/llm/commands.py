"""The `hub models ...` commands: see the catalog, add or remove a key, add a custom model.

Keys are read with a hidden prompt, stored only in the Keychain, and never printed, logged, or
written to a file. Each function returns an exit code and prints plain text. They take the key
store and prompts as arguments so tests can run them without a Keychain or a terminal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from careerhub.core.secrets import KeyStore
from careerhub.core.settings import ProviderSetting, Settings, save_settings
from careerhub.llm.catalog import (
    PERSONAL_TASKS,
    PUBLIC_TASKS,
    Preset,
    load_catalog,
    ollama_is_running,
    stale_warning,
    with_catalog,
)
from careerhub.llm.providers import api_key_name

#: Short names accepted by `hub models add` / `remove`, and the presets each one covers.
GROUPS = {
    "anthropic": ("claude-haiku", "claude-sonnet"),
    "openai": ("openai-cheap", "openai-strong"),
    "local": ("local-8b",),
}
CUSTOM_KINDS = ("anthropic", "openai", "google", "local")


@dataclass(frozen=True)
class _Choice:
    """The presets a command applies to, or a message saying why none were chosen."""

    presets: tuple[Preset, ...]
    error: str = ""


def _choose(name: str, tier: str | None) -> _Choice:
    """Resolve `name` (a group like "gemini" with its tier, or a preset name) to presets."""
    by_name = {p.name: p for p in load_catalog().presets}
    if name == "gemini":
        if tier not in ("free", "paid"):
            return _Choice(
                (), "Gemini needs --tier free or --tier paid (free trains on your data)."
            )
        return _Choice((by_name[f"gemini-{tier}"],))
    if tier is not None:
        return _Choice((), "--tier only applies to gemini.")
    names = GROUPS.get(name, (name,))
    if not all(n in by_name for n in names):
        known = ", ".join(sorted([*GROUPS, "gemini", *by_name]))
        return _Choice((), f"Unknown model {name!r}. Choose one of: {known}.")
    return _Choice(tuple(by_name[n] for n in names))


def _price(preset: Preset) -> str:
    if preset.input_per_mtok is None or preset.output_per_mtok is None:
        return "no price"
    return f"${preset.input_per_mtok:g} in / ${preset.output_per_mtok:g} out per million tokens"


def models_list(
    store: KeyStore, today: date, local_running: Callable[[], bool] = ollama_is_running
) -> int:
    """Print every preset, whether it is switched on, and its prices with the date checked."""
    catalog = load_catalog()
    for preset in catalog.presets:
        on = (
            local_running()
            if preset.kind == "local"
            else bool(store.get(api_key_name(preset.name)))
        )
        data = "trains on your data (public tasks only)" if preset.trains_on_data else "no training"
        state = "ON " if on else "off"
        print(f"{state} {preset.name:<14} {preset.model:<20} {_price(preset)}; {data}")
    print(f"Prices checked on {catalog.prices_checked}.")
    warning = stale_warning(today)
    if warning:
        print(f"Warning: {warning}")
    return 0


def models_add(
    store: KeyStore, name: str, tier: str | None, ask_secret: Callable[[str], str]
) -> int:
    """Ask for the API key (hidden) and save it in the Keychain for the chosen presets."""
    choice = _choose(name, tier)
    if choice.error:
        print(choice.error)
        return 1
    if choice.presets[0].kind == "local":
        print(
            "The local model needs no key. Install Ollama, run `ollama pull "
            f"{choice.presets[0].model}`, and keep it running."
        )
        return 0
    key = ask_secret(f"API key for {name} (input is hidden): ").strip()
    if not key:
        print("No key entered; nothing saved.")
        return 1
    for preset in choice.presets:
        store.set(api_key_name(preset.name), key)
    print(
        f"Saved the key in the Keychain. Switched on: {', '.join(p.name for p in choice.presets)}."
    )
    return 0


def models_remove(
    store: KeyStore, name: str, tier: str | None, confirm: Callable[[str], str]
) -> int:
    """After a yes/no question, delete the saved key for the chosen presets."""
    choice = _choose(name, tier)
    if choice.error:
        print(choice.error)
        return 1
    names = ", ".join(p.name for p in choice.presets)
    if confirm(f"Delete the saved key for {names}? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing removed.")
        return 1
    for preset in choice.presets:
        store.delete(api_key_name(preset.name))
    print(f"Removed the key for {names}.")
    return 0


def _why(error: Any) -> str:
    """Say what is wrong with one field, in plain words (never echoing a secret)."""
    field = str(error["loc"][0]) if error["loc"] else "model"
    detail = str(error.get("ctx", {}).get("error", "")).strip()
    return f"{field}: {detail}" if detail else field


def models_add_custom(
    store: KeyStore,
    settings: Settings,
    options: dict[str, str | bool],
    ask_secret: Callable[[str], str],
) -> int:
    """Add your own model (any OpenAI-compatible address, or another kind) to settings.

    `options` holds name, kind, model, and optionally base_url, input, output, no_training.
    A cloud model is assumed to train on your data unless `no_training` is set (CH-117).
    """
    taken = {p.name for p in settings.providers} | {p.name for p in load_catalog().presets}
    if options.get("name") in taken:
        print(f"The name {options.get('name')!r} is already used.")
        return 1
    try:
        kind = options.get("kind")
        provider = ProviderSetting.model_validate(
            {
                "name": options.get("name"),
                "kind": options.get("kind"),
                "model": options.get("model"),
                "base_url": options.get("base_url"),
                "input_per_mtok": options.get("input"),
                "output_per_mtok": options.get("output"),
                "trains_on_data": not options.get("no_training", False) and kind != "local",
            }
        )
    except ValidationError as error:
        print("Invalid model: " + "; ".join(_why(e) for e in error.errors()))
        return 1
    key_name = api_key_name(provider.name)
    previous = store.get(key_name)  # put it back if saving fails
    if provider.kind != "local":
        key = ask_secret(f"API key for {provider.name} (input is hidden): ").strip()
        if not key:
            print("No key entered; nothing saved.")
            return 1
        store.set(key_name, key)
    try:
        save_settings(settings.model_copy(update={"providers": (*settings.providers, provider)}))
    except BaseException:
        if provider.kind != "local":  # a failed save must not leave a key with no model
            if previous is None:
                store.delete(key_name)
            else:
                store.set(key_name, previous)
        raise
    print(f"Added {provider.name}. Add it to model_routes in settings.json to use it.")
    return 0


def models_routes(
    settings: Settings, store: KeyStore, local_running: Callable[[], bool] = ollama_is_running
) -> int:
    """Print the model each task will try, in order, and why."""
    setup = with_catalog(settings, store, local_running)
    for task in (*PUBLIC_TASKS, *PERSONAL_TASKS):
        route = setup.settings.model_routes.get(task)
        shown = " -> ".join(route) if route else "(no route)"
        print(f"{task:<16} {shown}   [{setup.reasons.get(task, '')}]")
    return 0
