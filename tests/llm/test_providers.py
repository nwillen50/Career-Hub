from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from careerhub.core.secrets import InMemoryKeyStore
from careerhub.llm import Destination, LLMRequest, PrivacyTier, Router
from careerhub.llm.providers import (
    AnthropicProvider,
    GoogleProvider,
    LocalProvider,
    MissingApiKey,
    OpenAIProvider,
    ProviderError,
    api_key_name,
    load_api_key,
)

REQUEST = LLMRequest(prompt="Say hi", tier=PrivacyTier.PUBLIC, system="Be brief", max_tokens=50)


def client(reply: dict[str, Any] | int, seen: list[httpx.Request] | None = None) -> httpx.Client:
    """A client that answers with a saved reply instead of using the network."""

    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(reply, int):
            return httpx.Response(reply, text="echo: Say hi")
        return httpx.Response(200, json=reply)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_anthropic_request_and_reply() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "content": [{"type": "text", "text": "Hi!"}],
        "usage": {"input_tokens": 9, "output_tokens": 2},
    }
    provider = AnthropicProvider("a", "m", "KEY", client=client(reply, seen))
    response = provider.complete(REQUEST)
    assert (response.text, response.input_tokens, response.output_tokens) == ("Hi!", 9, 2)
    sent = seen[0]
    body = json.loads(sent.content)
    assert sent.url.path == "/v1/messages" and sent.headers["x-api-key"] == "KEY"
    assert body["system"] == "Be brief" and body["max_tokens"] == 50
    assert provider.destination is Destination.CLOUD


def test_openai_request_and_reply() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": "Hi!"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    response = OpenAIProvider("o", "m", "KEY", client=client(reply, seen)).complete(REQUEST)
    body = json.loads(seen[0].content)
    assert (response.text, response.input_tokens, response.output_tokens) == ("Hi!", 7, 3)
    assert seen[0].headers["authorization"] == "Bearer KEY"
    assert body["messages"][0] == {"role": "system", "content": "Be brief"}


def test_google_request_and_reply_keeps_the_key_out_of_the_url() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "candidates": [{"content": {"parts": [{"text": "Hi"}, {"text": "!"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1},
    }
    response = GoogleProvider("g", "gem", "KEY", client=client(reply, seen)).complete(REQUEST)
    assert (response.text, response.input_tokens, response.output_tokens) == ("Hi!", 5, 1)
    assert "KEY" not in str(seen[0].url) and seen[0].headers["x-goog-api-key"] == "KEY"
    assert seen[0].url.path.endswith("/models/gem:generateContent")


def test_local_provider_is_local_and_sends_no_key() -> None:
    seen: list[httpx.Request] = []
    reply = {"choices": [{"message": {"content": "ok"}}]}
    local = LocalProvider("l", "llama", client=client(reply, seen))
    assert local.destination is Destination.LOCAL
    assert local.complete(REQUEST).text == "ok"
    assert "authorization" not in seen[0].headers


def test_a_local_provider_must_point_at_this_machine() -> None:
    with pytest.raises(ValueError):
        LocalProvider("l", "llama", base_url="https://example.com/v1")


def test_a_sensitive_call_reaches_local_but_not_cloud_through_the_router() -> None:
    from careerhub.llm import PrivacyViolation

    sensitive = LLMRequest(prompt="p", tier=PrivacyTier.SENSITIVE)
    reply = {"choices": [{"message": {"content": "ok"}}]}
    router = Router([LocalProvider("l", "m", client=client(reply)), OpenAIProvider("o", "m", "K")])
    assert router.complete(sensitive, "l").text == "ok"
    with pytest.raises(PrivacyViolation):
        router.complete(sensitive, "o")


def test_http_errors_never_include_the_response_body() -> None:
    with pytest.raises(ProviderError) as raised:
        OpenAIProvider("o", "m", "KEY", client=client(429)).complete(REQUEST)
    assert str(raised.value) == "o: HTTP 429 (busy)" and "Say hi" not in str(raised.value)


def test_a_reply_of_the_wrong_shape_is_a_provider_error() -> None:
    with pytest.raises(ProviderError):
        AnthropicProvider("a", "m", "K", client=client({"nope": 1})).complete(REQUEST)


def test_network_failure_is_a_provider_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    http = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(ProviderError, match="ConnectError"):
        GoogleProvider("g", "m", "K", client=http).complete(REQUEST)


def test_api_keys_come_from_the_key_store() -> None:
    store = InMemoryKeyStore()
    with pytest.raises(MissingApiKey, match="api-key-anthropic"):
        load_api_key(store, "anthropic")
    store.set(api_key_name("anthropic"), "KEY")
    assert load_api_key(store, "anthropic") == "KEY"
