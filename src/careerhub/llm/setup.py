"""Builds the router from the user's settings and Keychain keys.

`build_router` creates one adapter per configured provider, a response cache, and a cost log with
the monthly budget. A cloud provider whose API key isn't in the Keychain is left out and reported
in `problems` (so local models keep working); `routes_for(settings, "extract")` returns the
provider names a task should try, in order, keeping only providers that were actually built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.secrets import KeyStore
from careerhub.core.settings import ProviderSetting, Settings
from careerhub.llm.cache import ResponseCache
from careerhub.llm.cost import CostLog, Price
from careerhub.llm.provider import Provider
from careerhub.llm.providers import (
    AnthropicProvider,
    GoogleProvider,
    LocalProvider,
    MissingApiKey,
    OpenAIProvider,
    load_api_key,
)
from careerhub.llm.router import Router


class NoRoute(Exception):
    """A task has no usable provider (not configured, or its keys are missing)."""


@dataclass(frozen=True)
class RouterSetup:
    """The router, plus one line for each provider that couldn't be set up."""

    router: Router
    built: tuple[str, ...]
    problems: list[str] = field(default_factory=list)


def _build_provider(
    config: ProviderSetting, store: KeyStore, client: httpx.Client | None
) -> Provider:
    """Create the adapter for one provider setting (raises `MissingApiKey` for cloud ones)."""
    extra: dict[str, Any] = {"client": client} if client is not None else {}
    if config.base_url is not None:
        extra["base_url"] = config.base_url
    if config.kind == "local":
        return LocalProvider(config.name, config.model, **extra)
    key = load_api_key(store, config.name)
    common = {"trains_on_data": config.trains_on_data, **extra}
    if config.kind == "anthropic":
        return AnthropicProvider(config.name, config.model, key, **common)
    if config.kind == "openai":
        return OpenAIProvider(config.name, config.model, key, **common)
    return GoogleProvider(config.name, config.model, key, **common)


def build_router(
    settings: Settings,
    store: KeyStore,
    sessions: sessionmaker[Session],
    client: httpx.Client | None = None,
    run_id: int | None = None,
) -> RouterSetup:
    """Build the router with caching, cost logging, and the monthly budget from `settings`."""
    providers: list[Provider] = []
    problems: list[str] = []
    prices: dict[str, Price] = {}
    for config in settings.providers:
        try:
            providers.append(_build_provider(config, store, client))
        except (MissingApiKey, ValueError) as error:
            problems.append(str(error))
            continue
        if config.input_per_mtok is not None and config.output_per_mtok is not None:
            prices[config.name] = Price(config.input_per_mtok, config.output_per_mtok)
        elif config.kind != "local":
            problems.append(
                f"{config.name!r} has no prices (input_per_mtok, output_per_mtok), so its spending "
                "is not counted toward the monthly budget."
            )
    costs = CostLog(sessions, settings.monthly_budget_usd, prices, run_id)
    router = Router(providers, cache=ResponseCache(sessions), costs=costs)
    return RouterSetup(router, tuple(p.name for p in providers), problems)


def routes_for(settings: Settings, task: str, built: tuple[str, ...]) -> list[str]:
    """Return the providers `task` should try, in order, limited to those that were built.

    Raises `NoRoute` if the task has no route or none of its providers is available.
    """
    usable = [name for name in settings.model_routes.get(task, ()) if name in built]
    if not usable:
        raise NoRoute(
            f"No usable model for task {task!r}. Check model_routes and the providers' API keys."
        )
    return usable
