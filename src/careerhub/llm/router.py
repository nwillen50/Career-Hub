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
"""

from __future__ import annotations

from collections.abc import Iterable

from careerhub.llm.cache import ResponseCache
from careerhub.llm.cost import CostLog
from careerhub.llm.provider import Destination, Provider
from careerhub.llm.types import LLMRequest, LLMResponse, PrivacyTier


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
            f"{provider.name!r} may train on API data, so it can't see {tier.name} data."
        )


class Router:
    """Sends requests to named providers, after the privacy check."""

    def __init__(
        self,
        providers: Iterable[Provider],
        cache: ResponseCache | None = None,
        costs: CostLog | None = None,
    ) -> None:
        """Register providers by name (duplicates are an error).

        A `cache` answers repeated calls; a `costs` log records spending and enforces the budget.
        """
        self._cache = cache
        self._costs = costs
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

    def complete(self, request: LLMRequest, provider: str) -> LLMResponse:
        """Check privacy, then answer from the cache or send `request` to the provider.

        The privacy check always runs first, so a cached answer is never served to a provider
        that the request's tier forbids. The budget check comes after the cache, because a hit
        is free. Raises `BudgetExceeded` for a non-essential cloud call over budget.
        """
        chosen = self.provider(provider)
        check_allowed(request.tier, chosen)
        if self._cache is not None:
            hit = self._cache.get(chosen, request)
            if hit is not None:
                return hit
        if self._costs is not None:
            self._costs.check_budget(chosen, request.essential)
        response = chosen.complete(request)
        if self._costs is not None:
            self._costs.record(chosen, response)
        if self._cache is not None:
            self._cache.put(chosen, request, response)
        return response
