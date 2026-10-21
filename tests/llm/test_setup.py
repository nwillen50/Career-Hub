from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.migrate import migrate
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import ProviderSetting, Settings, SettingsError, load_settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import BudgetExceeded, Destination, LLMRequest, PrivacyTier, PrivacyViolation
from careerhub.llm.providers import api_key_name
from careerhub.llm.setup import NoRoute, build_router, routes_for

SETTINGS = {
    "monthly_budget_usd": 1.0,
    "providers": [
        {"name": "local-llama", "kind": "local", "model": "llama3"},
        {
            "name": "claude-cheap",
            "kind": "anthropic",
            "model": "m1",
            "input_per_mtok": 1.0,
            "output_per_mtok": 5.0,
        },
        {"name": "gpt", "kind": "openai", "model": "m2"},
    ],
    "model_routes": {"extract": ["claude-cheap", "local-llama"], "private": ["local-llama"]},
}


@pytest.fixture
def sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)


def reply_client(seen: list[httpx.Request]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.anthropic.com":
            body = {
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
            }
        else:
            body = {"choices": [{"message": {"content": "hi"}}]}
        return httpx.Response(200, content=json.dumps(body))

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_a_cloud_provider_without_a_key_is_left_out_and_reported(
    sessions: sessionmaker[Session],
) -> None:
    store = InMemoryKeyStore()
    store.set(api_key_name("claude-cheap"), "KEY")
    setup = build_router(Settings.model_validate(SETTINGS), store, sessions)
    assert setup.built == ("local-llama", "claude-cheap")
    assert len(setup.problems) == 1 and "api-key-gpt" in setup.problems[0]
    assert "KEY" not in setup.problems[0]


def test_routes_keep_only_built_providers_in_order(sessions: sessionmaker[Session]) -> None:
    settings = Settings.model_validate(SETTINGS)
    assert routes_for(settings, "extract", ("local-llama",)) == ["local-llama"]
    assert routes_for(settings, "extract", ("local-llama", "claude-cheap")) == [
        "claude-cheap",
        "local-llama",
    ]
    with pytest.raises(NoRoute):
        routes_for(settings, "extract", ("gpt",))
    with pytest.raises(NoRoute):
        routes_for(settings, "unknown-task", ("local-llama",))


def test_the_built_router_enforces_privacy_cache_and_budget(
    sessions: sessionmaker[Session],
) -> None:
    store = InMemoryKeyStore()
    store.set(api_key_name("claude-cheap"), "KEY")
    seen: list[httpx.Request] = []
    setup = build_router(Settings.model_validate(SETTINGS), store, sessions, reply_client(seen))
    router = setup.router
    assert router.provider("claude-cheap").destination is Destination.CLOUD
    sensitive = LLMRequest(prompt="p", tier=PrivacyTier.SENSITIVE)
    with pytest.raises(PrivacyViolation):
        router.complete(sensitive, "claude-cheap")
    assert seen == []  # refused before any request
    ask = LLMRequest(prompt="hello", tier=PrivacyTier.PUBLIC)
    assert router.complete(ask, "claude-cheap").text == "hi"  # costs $1: budget reached
    assert router.complete(ask, "claude-cheap").cached  # cache hit, no new request
    assert len(seen) == 1
    with pytest.raises(BudgetExceeded):
        router.complete(LLMRequest(prompt="other", tier=PrivacyTier.PUBLIC), "claude-cheap")


def test_settings_reject_unknown_routes_and_duplicate_names() -> None:
    bad_route = {**SETTINGS, "model_routes": {"extract": ["nope"]}}
    with pytest.raises(ValueError, match="unknown providers"):
        Settings.model_validate(bad_route)
    dup = {**SETTINGS, "providers": SETTINGS["providers"] + [SETTINGS["providers"][0]]}  # type: ignore[operator]
    with pytest.raises(ValueError, match="unique"):
        Settings.model_validate(dup)
    with pytest.raises(ValueError, match="no providers"):
        Settings.model_validate({**SETTINGS, "model_routes": {"extract": []}})


def test_settings_file_with_providers_loads_and_errors_omit_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(SETTINGS))
    assert len(load_settings(path).providers) == 3
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {"name": "x", "kind": "anthropic", "model": "m", "api_key": "SECRET-KEY"}
                ]
            }
        )
    )
    with pytest.raises(SettingsError) as raised:
        load_settings(path)
    assert "SECRET-KEY" not in str(raised.value)


def test_defaults_have_no_providers() -> None:
    settings = Settings()
    assert settings.providers == () and settings.model_routes == {}
    assert isinstance(ProviderSetting(name="a", kind="local", model="m").trains_on_data, bool)
