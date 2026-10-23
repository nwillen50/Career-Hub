"""Safer defaults for custom models and base URLs (CH-117)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import ProviderSetting, Settings, load_settings
from careerhub.llm import Destination, PrivacyTier, PrivacyViolation
from careerhub.llm.catalog import with_catalog
from careerhub.llm.commands import api_key_name
from careerhub.llm.fake import FakeProvider
from careerhub.llm.router import check_allowed


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-test-secret")
    return tmp_path


def test_a_custom_model_is_assumed_to_train_on_your_data() -> None:
    store = InMemoryKeyStore()
    base = ["models", "add-custom", "mine", "openai", "m", "--base-url", "https://api.example.com"]
    assert main(base, store) == 0
    assert load_settings().providers[0].trains_on_data is True


def test_no_training_flag_says_it_does_not() -> None:
    store = InMemoryKeyStore()
    argv = ["models", "add-custom", "mine", "openai", "m", "--no-training"]
    assert main(argv, store) == 0
    assert load_settings().providers[0].trains_on_data is False


def test_the_old_flag_is_refused() -> None:
    argv = ["models", "add-custom", "mine", "openai", "m", "--trains-on-data"]
    assert main(argv, InMemoryKeyStore()) == 2


def test_a_hand_edited_provider_without_the_field_is_treated_as_training() -> None:
    cloud = ProviderSetting.model_validate({"name": "a", "kind": "openai", "model": "m"})
    local = ProviderSetting.model_validate({"name": "b", "kind": "local", "model": "m"})
    assert cloud.trains_on_data is True
    assert local.trains_on_data is False  # a model on this Mac keeps data here


def test_personal_data_is_refused_by_an_assumed_training_model() -> None:
    setup = with_catalog(
        Settings(providers=(ProviderSetting(name="mine", kind="openai", model="m"),)),
        InMemoryKeyStore(),
        lambda: False,
    )
    (provider,) = [p for p in setup.settings.providers if p.name == "mine"]
    fake = FakeProvider("mine", Destination.CLOUD, trains_on_data=provider.trains_on_data)
    with pytest.raises(PrivacyViolation):
        check_allowed(PrivacyTier.PERSONAL, fake)


@pytest.mark.parametrize("url", ["http://api.example.com/v1", "https://", "ftp://x.com", "api.com"])
def test_a_cloud_base_url_must_be_https_with_a_host(url: str) -> None:
    with pytest.raises(ValidationError):
        ProviderSetting(name="a", kind="openai", model="m", base_url=url)


def test_https_and_local_addresses_are_accepted() -> None:
    ProviderSetting(name="a", kind="openai", model="m", base_url="https://api.example.com/v1")
    ProviderSetting(name="b", kind="local", model="m", base_url="http://localhost:11434/v1")


def test_a_preset_override_needs_https_unless_it_points_at_this_machine() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {"preset_overrides": {"claude-haiku": {"base_url": "http://x.com"}}}
        )
    Settings.model_validate({"preset_overrides": {"claude-haiku": {"base_url": "https://x.com"}}})
    Settings.model_validate(
        {"preset_overrides": {"ollama": {"base_url": "http://localhost:11434/v1"}}}
    )


def test_a_failed_settings_save_leaves_no_key_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryKeyStore()

    def boom(settings: Settings) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("careerhub.llm.commands.save_settings", boom)
    with pytest.raises(OSError):
        main(["models", "add-custom", "mine", "openai", "m"], store)
    assert store.get(api_key_name("mine")) is None


def test_a_cloud_preset_with_a_localhost_http_override_is_a_clear_settings_error() -> None:
    """Review: this passed settings validation, then crashed with a raw pydantic error."""
    from careerhub.core.settings import SettingsError

    settings = Settings.model_validate(
        {"preset_overrides": {"claude-haiku": {"base_url": "http://localhost:8080/v1"}}}
    )
    store = InMemoryKeyStore()
    store.set(api_key_name("claude-haiku"), "K")
    with pytest.raises(SettingsError, match="claude-haiku.*https"):
        with_catalog(settings, store, lambda: False)


def test_a_failed_save_keeps_a_key_that_was_already_there(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryKeyStore()
    store.set(api_key_name("mine"), "older-key")

    def boom(settings: Settings) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("careerhub.llm.commands.save_settings", boom)
    with pytest.raises(OSError):
        main(["models", "add-custom", "mine", "openai", "m"], store)
    assert store.get(api_key_name("mine")) == "older-key"


def test_the_refusal_message_says_how_to_mark_your_own_model() -> None:
    fake = FakeProvider("mine", Destination.CLOUD, trains_on_data=True)
    with pytest.raises(PrivacyViolation, match='"trains_on_data": false'):
        check_allowed(PrivacyTier.PERSONAL, fake)
