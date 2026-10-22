"""Tests for the built-in model catalog and default routes (CH-99)."""

from __future__ import annotations

from datetime import date

import pytest

from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import ProviderSetting, Settings
from careerhub.llm import Destination, LLMRequest, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.catalog import load_catalog, preset_names, stale_warning, with_catalog
from careerhub.llm.fake import FakeProvider
from careerhub.llm.providers import api_key_name


def store_with(*names: str) -> InMemoryKeyStore:
    store = InMemoryKeyStore()
    for name in names:
        store.set(api_key_name(name), "sk-test-not-real")
    return store


def routes(settings: Settings, store: InMemoryKeyStore, local: bool = False):  # type: ignore[no-untyped-def]
    return with_catalog(settings, store, lambda: local)


def test_the_catalog_loads_and_covers_every_provider_kind() -> None:
    catalog = load_catalog()
    assert {p.kind for p in catalog.presets} == {"anthropic", "openai", "google", "local"}
    assert len(preset_names()) == len(catalog.presets)  # names are unique
    gemini = {p.name: p for p in catalog.presets if p.kind == "google"}
    assert gemini["gemini-free"].trains_on_data and not gemini["gemini-paid"].trains_on_data
    assert all(not p.trains_on_data for p in catalog.presets if p.kind != "google")


def test_nothing_is_active_without_a_key() -> None:
    result = routes(Settings(), InMemoryKeyStore())
    assert result.active == () and result.settings.model_routes == {}
    assert "no model" in result.reasons["score"]


def test_only_gemini_free_has_no_route_for_personal_tasks_and_says_so() -> None:
    result = routes(Settings(), store_with("gemini-free"))
    assert result.settings.model_routes == {"extract": ("gemini-free",)}
    assert "personal data" in result.reasons["score"]
    assert "no model that may see personal data" in result.reasons["tailor"]


def test_gemini_free_plus_haiku_splits_public_and_personal_work() -> None:
    result = routes(Settings(), store_with("gemini-free", "claude-haiku", "claude-sonnet"))
    assert result.settings.model_routes["extract"] == (
        "gemini-free",
        "claude-haiku",
        "claude-sonnet",
    )
    for task in ("score", "tailor", "profile_extract"):
        assert result.settings.model_routes[task] == ("claude-haiku", "claude-sonnet")


def test_local_only_handles_everything_and_is_last_otherwise() -> None:
    local_only = routes(Settings(), InMemoryKeyStore(), local=True)
    assert all(r == ("local-8b",) for r in local_only.settings.model_routes.values())
    mixed = routes(Settings(), store_with("claude-haiku"), local=True)
    assert mixed.settings.model_routes["score"] == ("claude-haiku", "local-8b")


def test_the_users_own_routes_and_providers_win() -> None:
    own = ProviderSetting(name="claude-haiku", kind="anthropic", model="custom-model")
    settings = Settings(providers=(own,), model_routes={"score": ("claude-haiku",)})
    result = routes(settings, store_with("claude-haiku", "claude-sonnet"))
    assert [p.model for p in result.settings.providers if p.name == "claude-haiku"] == [
        "custom-model"
    ]
    assert result.settings.model_routes["score"] == ("claude-haiku",)
    assert result.reasons["score"] == "set in settings"


def test_overrides_change_a_presets_model_and_prices() -> None:
    settings = Settings.model_validate(
        {"preset_overrides": {"claude-haiku": {"model": "claude-haiku-6", "input_per_mtok": 0.2}}}
    )
    haiku = next(p for p in routes(settings, store_with("claude-haiku")).settings.providers)
    assert (haiku.model, haiku.input_per_mtok, haiku.output_per_mtok) == (
        "claude-haiku-6",
        0.2,
        0.5,
    )


def test_privacy_fields_cannot_be_overridden() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"preset_overrides": {"gemini-free": {"trains_on_data": False}}})


def test_routes_may_name_a_preset_but_not_an_unknown_model() -> None:
    Settings(model_routes={"score": ("claude-haiku",)})
    with pytest.raises(ValueError, match="unknown providers"):
        Settings(model_routes={"score": ("nonsense",)})


def test_the_router_still_refuses_personal_data_to_gemini_free() -> None:
    free = FakeProvider("gemini-free", destination=Destination.CLOUD, trains_on_data=True)
    with pytest.raises(PrivacyViolation):
        Router([free]).complete(LLMRequest(prompt="x", tier=PrivacyTier.PERSONAL), "gemini-free")


def test_keys_are_not_in_the_resulting_settings() -> None:
    result = routes(Settings(), store_with("claude-haiku"))
    assert "sk-test-not-real" not in result.settings.model_dump_json()


def test_stale_prices_are_flagged_after_90_days() -> None:
    assert stale_warning(date(2026, 11, 1)) is None
    assert "2026-10-22" in (stale_warning(date(2027, 3, 1)) or "")
