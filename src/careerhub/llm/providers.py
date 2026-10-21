"""Real model providers: Anthropic, OpenAI, Google, and local (OpenAI-compatible) servers.

Each adapter turns an `LLMRequest` into one HTTPS call and the reply into an `LLMResponse`.
They do no privacy checking (the `Router` does) and never log prompts, replies, or keys. An HTTP
failure raises `ProviderError` carrying only the status code, because an error body can echo the
prompt. Tests pass an `httpx.Client` with a mock transport, so they never touch the network.

API keys live in the Keychain under `api_key_name(provider_name)`; use `load_api_key`.
"""

from __future__ import annotations

from typing import Any

import httpx

from careerhub.core.secrets import KeyStore
from careerhub.llm.provider import Destination
from careerhub.llm.types import LLMRequest, LLMResponse

#: Seconds to wait for a model before giving up.
TIMEOUT_SECONDS = 120.0


class ProviderError(Exception):
    """A provider call failed. The message has the status code only, never the response body."""


class MissingApiKey(Exception):
    """No API key is stored for this provider."""


def api_key_name(provider_name: str) -> str:
    """Return the Keychain entry name holding `provider_name`'s API key."""
    return f"api-key-{provider_name}"


def load_api_key(store: KeyStore, provider_name: str) -> str:
    """Return the stored API key, or raise `MissingApiKey` naming the Keychain entry."""
    key = store.get(api_key_name(provider_name))
    if not key:
        raise MissingApiKey(
            f"No API key for {provider_name!r}: add it to the Keychain as "
            f"{api_key_name(provider_name)!r}."
        )
    return key


class _HttpProvider:
    """Shared plumbing: one JSON POST, with a safe error."""

    destination = Destination.CLOUD
    trains_on_data = False

    def __init__(
        self,
        name: str,
        model: str,
        api_key: str | None = None,
        *,
        base_url: str,
        trains_on_data: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        """Set up the adapter; pass `client` in tests to replace the network."""
        self.name = name
        self.model = model
        self.trains_on_data = trains_on_data
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)

    def _post(self, path: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """POST `body` as JSON and return the decoded reply."""
        try:
            reply = self._client.post(f"{self._base_url}{path}", json=body, headers=headers)
        except httpx.HTTPError as error:
            raise ProviderError(f"{self.name}: request failed ({type(error).__name__})") from None
        if reply.status_code != 200:
            raise ProviderError(f"{self.name}: HTTP {reply.status_code}")
        try:
            data: dict[str, Any] = reply.json()
        except ValueError:
            raise ProviderError(f"{self.name}: reply was not JSON") from None
        return data

    def _bad_shape(self) -> ProviderError:
        return ProviderError(f"{self.name}: reply had an unexpected shape")


class AnthropicProvider(_HttpProvider):
    """Claude models through the Anthropic Messages API."""

    def __init__(self, name: str, model: str, api_key: str, **kwargs: Any) -> None:
        """Create the adapter; `base_url` defaults to Anthropic's API."""
        kwargs.setdefault("base_url", "https://api.anthropic.com")
        super().__init__(name, model, api_key, **kwargs)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send the request and return the model's text."""
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            body["system"] = request.system
        data = self._post(
            "/v1/messages",
            body,
            {"x-api-key": self._api_key or "", "anthropic-version": "2023-06-01"},
        )
        try:
            text = "".join(b["text"] for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage", {})
            return LLMResponse(
                text,
                self.name,
                self.model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )
        except (KeyError, TypeError, AttributeError):
            raise self._bad_shape() from None


class OpenAIProvider(_HttpProvider):
    """OpenAI models, or any server speaking the same chat API (set `base_url`)."""

    def __init__(self, name: str, model: str, api_key: str | None, **kwargs: Any) -> None:
        """Create the adapter; `base_url` defaults to OpenAI's API."""
        kwargs.setdefault("base_url", "https://api.openai.com/v1")
        super().__init__(name, model, api_key, **kwargs)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send the request and return the model's text."""
        messages = [{"role": "user", "content": request.prompt}]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        headers = {"authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        data = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "max_completion_tokens": request.max_tokens,
            },
            headers,
        )
        try:
            usage = data.get("usage", {})
            return LLMResponse(
                data["choices"][0]["message"]["content"],
                self.name,
                self.model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
        except (KeyError, IndexError, TypeError):
            raise self._bad_shape() from None


class LocalProvider(OpenAIProvider):
    """A model running on this Mac (Ollama, LM Studio, llama.cpp) via its OpenAI-style server.

    Its destination is LOCAL, so it may see every privacy tier. The URL must point at this
    machine; anything else is refused, so "local" can't be misconfigured into a leak.
    """

    destination = Destination.LOCAL

    def __init__(
        self,
        name: str,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        **kwargs: Any,
    ) -> None:
        """Create the adapter; raises ValueError if `base_url` isn't on this machine."""
        host = httpx.URL(base_url).host
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(f"A local provider must use localhost, not {host!r}.")
        super().__init__(name, model, None, base_url=base_url, **kwargs)


class GoogleProvider(_HttpProvider):
    """Gemini models through the Google Generative Language API."""

    def __init__(self, name: str, model: str, api_key: str, **kwargs: Any) -> None:
        """Create the adapter; `base_url` defaults to Google's API."""
        kwargs.setdefault("base_url", "https://generativelanguage.googleapis.com/v1beta")
        super().__init__(name, model, api_key, **kwargs)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send the request and return the model's text."""
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {"maxOutputTokens": request.max_tokens},
        }
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        # The key goes in a header, not the URL, so it can't end up in a logged address.
        data = self._post(
            f"/models/{self.model}:generateContent", body, {"x-goog-api-key": self._api_key or ""}
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            usage = data.get("usageMetadata", {})
            return LLMResponse(
                "".join(p.get("text", "") for p in parts),
                self.name,
                self.model,
                usage.get("promptTokenCount", 0),
                usage.get("candidatesTokenCount", 0),
            )
        except (KeyError, IndexError, TypeError):
            raise self._bad_shape() from None
