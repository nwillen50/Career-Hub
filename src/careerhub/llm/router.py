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
row is benched for `BENCH_SECONDS`, then tried once more, so 1,500 postings don't each
wait out a timeout before falling back to the next model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from careerhub.llm.cache import ResponseCache
from careerhub.llm.cost import CostLog
from careerhub.llm.provider import Destination, Provider
from careerhub.llm.providers import ProviderError
from careerhub.llm.types import LLMRequest, LLMResponse, PrivacyTier

#: Consecutive failures after which a provider is skipped for the rest of the run.
BENCH_AFTER = 3
#: How long a benched provider is skipped before one call is allowed to test it again.
BENCH_SECONDS = 600.0
#: Seconds to wait before retrying a call the provider rate limited (HTTP 429).
RATE_LIMIT_WAIT_SECONDS = 20.0


@dataclass(frozen=True)
class RateLimit:
    """A provider's request limits (a free tier allows only a few a minute and a day)."""

    per_minute: int | None = None
    per_day: int | None = None


#: HTTP statuses that blame the one request, not the provider, so they never bench it.
_REQUEST_SPECIFIC = frozenset({400, 404, 413, 422})


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
        limits: dict[str, RateLimit] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Register providers by name (duplicates are an error).

        A `cache` answers repeated calls; a `costs` log records spending and enforces the budget.
        """
        self._cache = cache
        self._costs = costs
        self._sleep = sleep
        self._clock = clock
        self._limits = limits or {}
        self._sent: dict[str, int] = {}
        self._last_sent: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._benched_at: dict[str, float] = {}
        self._seeded: set[str] = set()
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
            if self._clock() - self._benched_at[name] < BENCH_SECONDS:
                raise ProviderError(
                    f"{name}: benched", f"skipped for a while after {BENCH_AFTER} failures in a row"
                )
            self._failures[name] = BENCH_AFTER - 1  # try once; one more failure benches it again
        self._pace(name)
        try:
            try:
                response = provider.complete(request)
            except ProviderError as error:
                if error.status != 429:
                    raise
                self._sleep(RATE_LIMIT_WAIT_SECONDS)
                self._pace(name)  # the retry is a real request: count it against the limits
                response = provider.complete(request)
        except ProviderError as error:
            if error.status in _REQUEST_SPECIFIC:
                raise  # this posting was refused (too long, ...); the model itself is fine
            self._failures[name] = self._failures.get(name, 0) + 1
            if self._failures[name] == BENCH_AFTER:
                self._benched_at[name] = self._clock()
                logging.getLogger("careerhub.llm").warning(
                    "model %s failed %d times in a row; skipped for %d seconds",
                    name,
                    BENCH_AFTER,
                    int(BENCH_SECONDS),
                )
            raise
        self._failures[name] = 0
        return response

    def _pace(self, name: str) -> None:
        """Keep to the provider's limits: space calls out, and stop at the daily cap.

        The daily count starts from the calls already logged today (earlier runs count), so a
        second run the same day does not spend the quota again.
        """
        limit = self._limits.get(name)
        if limit is None:
            return
        if name not in self._seeded:
            self._seeded.add(name)
            if self._costs is not None:
                self._sent[name] = self._costs.calls_today(name)
        if limit.per_day is not None and self._sent.get(name, 0) >= limit.per_day:
            raise ProviderError(
                f"{name}: daily limit",
                f"daily limit of {limit.per_day} requests reached; the rest waits for tomorrow",
            )
        if limit.per_minute is not None and name in self._last_sent:
            wait = 60.0 / limit.per_minute - (self._clock() - self._last_sent[name])
            if wait > 0:
                self._sleep(wait)
        self._last_sent[name] = self._clock()
        self._sent[name] = self._sent.get(name, 0) + 1
