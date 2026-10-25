"""Tests for the `hub models` commands (CH-108). No Keychain and no terminal are used."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings, load_settings
from careerhub.llm.providers import api_key_name

SECRET = "sk-secret-value-123"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("careerhub.cli.getpass.getpass", lambda prompt: SECRET)
    monkeypatch.setattr("careerhub.llm.commands.ollama_is_running", lambda client=None: False)
    return tmp_path


def test_add_anthropic_stores_one_key_for_both_models_and_never_prints_it(
    capsys: pytest.CaptureFixture[str], data_dir: Path
) -> None:
    store = InMemoryKeyStore()
    assert main(["models", "add", "anthropic"], store) == 0
    out = capsys.readouterr().out
    assert SECRET not in out and "claude-haiku" in out
    assert store.get(api_key_name("claude-haiku")) == SECRET
    assert store.get(api_key_name("claude-sonnet")) == SECRET
    assert all(SECRET not in p.read_text() for p in data_dir.rglob("*") if p.is_file())


def test_gemini_needs_an_explicit_tier(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryKeyStore()
    assert main(["models", "add", "gemini"], store) == 1
    assert "--tier" in capsys.readouterr().out
    assert main(["models", "add", "gemini", "--tier", "free"], store) == 0
    assert store.get(api_key_name("gemini-free")) == SECRET
    assert store.get(api_key_name("gemini-paid")) is None


def test_remove_asks_first_then_deletes_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = InMemoryKeyStore()
    main(["models", "add", "openai"], store)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["models", "remove", "openai"], store) == 1
    assert store.get(api_key_name("openai-cheap")) == SECRET
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["models", "remove", "openai"], store) == 0
    assert store.get(api_key_name("openai-cheap")) is None
    assert "Removed" in capsys.readouterr().out


def test_unknown_model_and_a_tier_on_the_wrong_model_are_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["models", "add", "nonsense"], InMemoryKeyStore()) == 1
    assert main(["models", "add", "anthropic", "--tier", "free"], InMemoryKeyStore()) == 1
    assert "Unknown model" in capsys.readouterr().out


def test_list_shows_state_prices_and_the_checked_date(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from careerhub.llm.commands import models_list

    store = InMemoryKeyStore()
    main(["models", "add", "gemini", "--tier", "free"], store)
    capsys.readouterr()
    assert models_list(store, date(2026, 11, 1), lambda: False) == 0
    out = capsys.readouterr().out
    assert "ON  gemini-free" in out and "off claude-haiku" in out
    assert "Prices checked on 2026-10-24." in out and "Warning" not in out
    models_list(store, date(2027, 6, 1), lambda: False)
    assert "Warning" in capsys.readouterr().out


def test_routes_show_the_reason_and_no_route_for_personal_tasks_with_gemini_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = InMemoryKeyStore()
    main(["models", "add", "gemini", "--tier", "free"], store)
    capsys.readouterr()
    assert main(["models", "routes"], store) == 0
    out = capsys.readouterr().out
    assert "extract" in out and "gemini-free" in out
    assert "(no route)" in out and "no model that may see personal data" in out


def test_add_custom_saves_a_provider_and_the_key_only_in_the_store(data_dir: Path) -> None:
    store = InMemoryKeyStore()
    argv = ["models", "add-custom", "my-llm", "openai", "some-model", "--base-url"]
    url = "https://llm.example.com/v1"
    assert main([*argv, url, "--input", "0.5", "--output", "1", "--no-training"], store) == 0
    provider = load_settings().providers[0]
    assert (provider.name, provider.trains_on_data, provider.input_per_mtok) == (
        "my-llm",
        False,
        0.5,
    )
    assert store.get(api_key_name("my-llm")) == SECRET
    assert SECRET not in (data_dir / "settings.json").read_text()
    assert main(["models", "add-custom", "my-llm", "openai", "m"], store) == 1  # name taken
    assert main(["models", "add-custom", "claude-haiku", "openai", "m"], store) == 1  # preset name
    assert main(["models", "add-custom", "bad", "nonsense", "m"], store) == 1
    assert load_settings() == Settings(providers=load_settings().providers)


def test_a_dangling_flag_prints_usage() -> None:
    assert main(["models", "add", "anthropic", "--tier"], InMemoryKeyStore()) == 2


@pytest.mark.parametrize(
    "flag",
    [["--train-on-data", "yes"], ["--trainsondata", "yes"], ["--name", "x"], ["--kind", "y"]],
)
def test_an_unknown_or_overriding_flag_is_refused_not_silently_dropped(flag: list[str]) -> None:
    from careerhub.core.settings import load_settings

    store = InMemoryKeyStore()
    assert main(["models", "add-custom", "g", "google", "m", *flag], store) == 2
    assert load_settings().providers == () and store.get(api_key_name("g")) is None
