"""The recommended configuration loads, routes as documented, and falls back safely (CH-84)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings
from careerhub.llm import LLMRequest, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.catalog import with_catalog
from careerhub.llm.cost import BudgetExceeded
from careerhub.llm.fake import FakeProvider
from careerhub.llm.provider import Destination
from careerhub.llm.providers import ProviderError, api_key_name
from careerhub.llm.validated import complete_validated

EXAMPLE = Path(__file__).parents[2] / "docs" / "examples" / "settings.recommended.json"


class Answer(BaseModel):
    ok: bool


class Failing(FakeProvider):
    """A provider whose every call fails with the given error."""

    def __init__(self, name: str, error: Exception, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, **kwargs)
        self._error = error

    def complete(self, request: LLMRequest):  # type: ignore[no-untyped-def]
        raise self._error


def configured() -> Settings:
    return Settings.model_validate(json.loads(EXAMPLE.read_text()))


def test_the_example_loads_with_a_five_dollar_budget_and_the_documented_routes() -> None:
    settings = configured()
    assert settings.monthly_budget_usd == 5
    assert settings.model_routes["extract"] == ("gemini-free", "local-8b")
    assert settings.model_routes["score"] == ("claude-haiku", "claude-sonnet")
    assert settings.model_routes["tailor"] == ("claude-haiku", "local-8b")
    assert settings.model_routes["profile_extract"] == ("claude-haiku", "local-8b")
    assert settings.model_routes["chat"] == ("claude-haiku", "local-8b")


def test_with_the_three_keys_every_routed_model_is_switched_on() -> None:
    store = InMemoryKeyStore()
    for name in ("claude-haiku", "claude-sonnet", "gemini-free"):
        store.set(api_key_name(name), "k")
    setup = with_catalog(configured(), store, lambda: True)
    assert set(setup.active) == {"claude-haiku", "claude-sonnet", "gemini-free", "local-8b"}
    assert setup.settings.model_routes == configured().model_routes  # the user's routes win


def test_no_personal_task_is_routed_to_gemini_free() -> None:
    routes = configured().model_routes
    assert all("gemini-free" not in r for task, r in routes.items() if task != "extract")


def test_the_router_refuses_personal_data_to_gemini_free() -> None:
    free = FakeProvider("gemini-free", Destination.CLOUD, trains_on_data=True)
    with pytest.raises(PrivacyViolation):
        Router([free]).complete(LLMRequest(prompt="x", tier=PrivacyTier.PERSONAL), "gemini-free")


def test_a_rate_limited_gemini_falls_through_to_the_local_model_and_is_noted() -> None:
    gemini = Failing("gemini-free", ProviderError("HTTP 429"), destination=Destination.CLOUD)
    local = FakeProvider("local-8b", replies=['{"ok": true}'])
    router = Router([gemini, local])
    request = LLMRequest(prompt="x", tier=PrivacyTier.PUBLIC)
    assert complete_validated(router, request, Answer, ["gemini-free", "local-8b"]).ok
    assert router.skipped == ["gemini-free: HTTP 429"]


def test_tailoring_falls_back_to_local_when_haiku_is_paused_by_the_budget() -> None:
    haiku = Failing("claude-haiku", BudgetExceeded("over"), destination=Destination.CLOUD)
    local = FakeProvider("local-8b", replies=['{"ok": true}'])
    router = Router([haiku, local])
    request = LLMRequest(prompt="x", tier=PrivacyTier.PERSONAL, essential=False)
    assert complete_validated(router, request, Answer, ["claude-haiku", "local-8b"]).ok
    assert router.skipped == ["claude-haiku: BudgetExceeded"]


def test_when_every_model_is_unavailable_the_error_is_raised_not_swallowed() -> None:
    router = Router([Failing("a", ProviderError("HTTP 500"), destination=Destination.CLOUD)])
    with pytest.raises(ProviderError):
        complete_validated(router, LLMRequest(prompt="x", tier=PrivacyTier.PUBLIC), Answer, ["a"])


def test_a_privacy_refusal_never_falls_through_to_another_provider() -> None:
    free = FakeProvider("gemini-free", Destination.CLOUD, trains_on_data=True)
    local = FakeProvider("local-8b", replies=['{"ok": true}'])
    request = LLMRequest(prompt="x", tier=PrivacyTier.PERSONAL)
    with pytest.raises(PrivacyViolation):
        complete_validated(Router([free, local]), request, Answer, ["gemini-free", "local-8b"])
