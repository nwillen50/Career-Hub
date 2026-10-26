"""Model failures say why (short safe reasons), the log file, and `hub models check` (CH-209)."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings
from careerhub.llm.commands import models_check
from careerhub.llm.providers import OpenAIProvider, ProviderError
from careerhub.llm.router import Router
from careerhub.llm.types import LLMRequest, PrivacyTier
from careerhub.llm.validated import complete_validated

REQUEST = LLMRequest(prompt="Say hi", tier=PrivacyTier.PUBLIC)
KEY = "sk-super-secret-key-value"


class Answer(BaseModel):
    ok: bool


def failing_client(status: int) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=KEY)))


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "HTTP 401 (key rejected)"),
        (400, "HTTP 400 (often no credit)"),
        (404, "HTTP 404 (model id unknown)"),
        (429, "HTTP 429 (busy)"),
        (529, "HTTP 529 (busy)"),
        (500, "HTTP 500"),
    ],
)
def test_each_status_has_a_short_reason_and_no_body(status: int, reason: str) -> None:
    with pytest.raises(ProviderError) as raised:
        OpenAIProvider("o", "m", KEY, client=failing_client(status)).complete(REQUEST)
    assert raised.value.reason == reason
    assert KEY not in str(raised.value)


def test_a_network_failure_reason_names_only_the_error_type() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret detail")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(ProviderError) as raised:
        OpenAIProvider("o", "m", KEY, client=client).complete(REQUEST)
    assert raised.value.reason == "request failed (ConnectError)"


def test_the_router_note_and_log_carry_the_reason(caplog: pytest.LogCaptureFixture) -> None:
    router = Router([OpenAIProvider("claude-haiku", "m", KEY, client=failing_client(401))])
    with caplog.at_level(logging.WARNING, logger="careerhub.llm"):
        with pytest.raises(ProviderError):
            complete_validated(router, REQUEST, Answer, ["claude-haiku"])
    assert router.skipped == ["claude-haiku: HTTP 401 (key rejected)"]
    assert "claude-haiku skipped: HTTP 401 (key rejected)" in caplog.text
    assert KEY not in caplog.text


def test_a_command_that_runs_the_pipeline_creates_a_private_log_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    try:
        assert main(["refresh"], InMemoryKeyStore()) in (0, 1)
        logging.getLogger("careerhub.test").warning("write to %s now", "me@example.com")
        for handler in logging.getLogger("careerhub").handlers:
            handler.flush()
        log = tmp_path / "logs" / "careerhub.log"
        assert log.exists() and stat.S_IMODE(log.stat().st_mode) == 0o600
        assert "[email]" in log.read_text() and "me@example.com" not in log.read_text()
    finally:
        for handler in list(logging.getLogger("careerhub").handlers):
            logging.getLogger("careerhub").removeHandler(handler)
            handler.close()


def test_models_check_prints_ok_or_the_reason(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careerhub.core.migrate import migrate
    from careerhub.core.storage import open_database, session_factory

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    store.set("api-key-good", KEY)
    store.set("api-key-bad", KEY)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/bad/"):
            return httpx.Response(401)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    # one shared client; the two providers differ by base_url path
    client = httpx.Client(transport=httpx.MockTransport(handle))
    settings = Settings.model_validate(
        {
            "providers": [
                {
                    "name": n,
                    "kind": "openai",
                    "model": "m",
                    "base_url": f"https://x.test/{n}",
                    "input_per_mtok": 1,
                    "output_per_mtok": 1,
                }
                for n in ("good", "bad")
            ]
        }
    )
    engine = open_database(store)
    migrate(engine)
    models_check(settings, store, session_factory(engine), client)
    out = capsys.readouterr().out
    assert code == 1
    assert "good: OK" in out and "bad: FAILED HTTP 401 (key rejected)" in out
    assert KEY not in out


def test_the_log_stays_private_after_it_rotates(tmp_path: Path) -> None:
    import os

    from careerhub.core.logging import configure_logging

    old = os.umask(0o022)
    try:
        path = tmp_path / "logs" / "careerhub.log"
        log = configure_logging(log_file=path)
        for _ in range(12_000):
            log.info("x" * 100)
        assert path.with_name("careerhub.log.1").exists()  # it rotated
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        os.umask(old)
        for handler in list(logging.getLogger("careerhub").handlers):
            logging.getLogger("careerhub").removeHandler(handler)
            handler.close()


def test_models_check_goes_through_the_router_so_the_budget_pauses_it(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from careerhub.core.migrate import migrate
    from careerhub.core.storage import open_database, session_factory

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    store.set("api-key-good", KEY)
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    settings = Settings.model_validate(
        {
            "monthly_budget_usd": 0,
            "providers": [
                {"name": "good", "kind": "openai", "model": "m", "base_url": "https://x.test/good",
                 "input_per_mtok": 1, "output_per_mtok": 1}
            ],
        }
    )  # fmt: skip
    engine = open_database(store)
    migrate(engine)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    models_check(settings, store, session_factory(engine), client)
    assert code == 1 and calls == []  # paused by the budget, no call made
    assert "budget" in capsys.readouterr().out


def test_models_check_includes_the_built_in_models_switched_on_by_a_key(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug CH-217: with keys for built-in models, check said 'No models are set up'."""
    from careerhub.core.migrate import migrate
    from careerhub.core.storage import open_database, session_factory

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    store.set("api-key-claude-haiku", KEY)

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "pong"}],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    engine = open_database(store)
    migrate(engine)
    models_check(
        Settings(),
        store,
        session_factory(engine),
        httpx.Client(transport=httpx.MockTransport(handle)),
        local_running=lambda: False,
    )
    out = capsys.readouterr().out
    assert "No models are set up" not in out
    assert "claude-haiku: OK" in out
