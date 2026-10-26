"""The router: the single choke point where every model call is checked.

`Router.complete` refuses a call before it reaches a provider if the provider isn't allowed to
see the call's privacy tier (PRD, "Privacy tiers"):

| Tier      | Local provider | Cloud provider                                    |
| --------- | -------------- | ------------------------------------------------- |
| PUBLIC    | allowed        | allowed                                           |
| PERSONAL  | allowed        | allowed unless the provider trains on API data    |
| SENSITIVE | allowed        | refused                                           |

There is no fallback: a refused call raises `PrivacyViolation` and never tries another provider
on its own, so a privacy rule can't be bypassed by "retry elsewhere".

Two things keep a big run from hammering a provider that can't answer (CH-216): a rate-limited
call (HTTP 429) waits and is retried once, and a provider that fails `BENCH_AFTER` calls in a
row is benched for the rest of this Router's life (one nightly run), so 1,500 postings don't each
wait out a timeout before falling back to the next model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

from careerhub.llm.cache import ResponseCache
from careerhub.llm.cost import CostLog
from careerhub.llm.provider import Destination, Provider
from careerhub.llm.providers import ProviderError
from careerhub.llm.types import LLMRequest, LLMResponse, PrivacyTier

#: Consecutive failures after which a provider is skipped for the rest of the run.
BENCH_AFTER = 3
#: Seconds to wait before retrying a call the provider rate limited (HTTP 429).
RATE_LIMIT_WAIT_SECONDS = 20.0


class PrivacyViolation(Exception):
    """The call's data is too sensitive for the chosen provider."""


class UnknownProvider(KeyError):
    """No provider with that name is registered."""


def check_allowed(tier: PrivacyTier, provider: Provider) -> None:
    """Raise `PrivacyViolation` unless `provider` may see data of this `tier`."""
    if provider.destination is Destination.LOCAL:
        return
    if tier >= PrivacyTier.SENSITIVE:
        raise PrivacyViolation(
            f"{tier.name} data stays on this Mac; {provider.name!r} is a cloud provider."
        )
    if tier >= PrivacyTier.PERSONAL and provider.trains_on_data:
        raise PrivacyViolation(
            f"{provider.name!r} may train on API data, so it can't see {tier.name} data. "
            'For a model of your own that does not train on your data, set "trains_on_data": false '
            "for it in settings.json."
        )


class Router:
    """Sends requests to named providers, after the privacy check."""

    def __init__(
        self,
        providers: Iterable[Provider],
        cache: ResponseCache | None = None,
        costs: CostLog | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Register providers by name (duplicates are an error).

        A `cache` answers repeated calls; a `costs` log records spending and enforces the budget.
        """
        self._cache = cache
        self._costs = costs
        self._sleep = sleep
        self._failures: dict[str, int] = {}
        #: Providers skipped because they were down, rate limited, or over budget ("name: Type").
        self.skipped: list[str] = []
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            if provider.name in self._providers:
                raise ValueError(f"Two providers are named {provider.name!r}.")
            self._providers[provider.name] = provider

    def provider(self, name: str) -> Provider:
        """Return the provider registered as `name`."""
        try:
            return self._providers[name]
        except KeyError:
            raise UnknownProvider(name) from None

    def complete(
        self,
        request: LLMRequest,
        provider: str,
        accept: Callable[[LLMResponse], bool] | None = None,
    ) -> LLMResponse:
        """Check privacy, then answer from the cache or send `request` to the provider.

        The privacy check always runs first, so a cached answer is never served to a provider
        that the request's tier forbids. The budget check comes after the cache, because a hit
        is free. Raises `BudgetExceeded` for a non-essential cloud call over budget.

        `accept` lets a caller say whether an answer is usable (for example, valid JSON). Only
        accepted answers are saved in the cache, and a cached answer that isn't accepted is
        ignored and replaced, so one bad answer can't be replayed on every later run.
        """
        chosen = self.provider(provider)
        check_allowed(request.tier, chosen)
        if self._cache is not None:
            hit = self._cache.get(chosen, request)
            if hit is not None and (accept is None or accept(hit)):
                return hit
        if self._costs is not None:
            self._costs.check_budget(chosen, request.essential)
        response = self._call(chosen, request)
        if self._costs is not None:
            self._costs.record(chosen, response)
        if self._cache is not None and (accept is None or accept(response)):
            self._cache.put(chosen, request, response)
        return response

    def _call(self, provider: Provider, request: LLMRequest) -> LLMResponse:
        """Call `provider`, retrying once after a 429 and benching it after repeated failures."""
        name = provider.name
        if self._failures.get(name, 0) >= BENCH_AFTER:
            raise ProviderError(
                f"{name}: benched", f"skipped for this run after {BENCH_AFTER} failures in a row"
            )
        try:
            try:
                response = provider.complete(request)
            except ProviderError as error:
                if error.status != 429:
                    raise
                self._sleep(RATE_LIMIT_WAIT_SECONDS)
                response = provider.complete(request)
        except ProviderError:
            self._failures[name] = self._failures.get(name, 0) + 1
            if self._failures[name] == BENCH_AFTER:
                logging.getLogger("careerhub.llm").warning(
                    "model %s failed %d times in a row; skipped for the rest of this run",
                    name,
                    BENCH_AFTER,
                )
            raise
        self._failures[name] = 0
        return response
