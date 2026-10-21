"""A scripted provider for tests: no network, no model, fully predictable."""

from __future__ import annotations

from careerhub.llm.provider import Destination
from careerhub.llm.types import LLMRequest, LLMResponse


class FakeProvider:
    """Returns canned answers in order (repeating the last) and records every request it gets."""

    def __init__(
        self,
        name: str = "fake",
        destination: Destination = Destination.LOCAL,
        trains_on_data: bool = False,
        replies: list[str] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Create a fake. `replies` default to one "ok"; every reply reports the token counts."""
        self.name = name
        self.model = f"{name}-model"
        self.destination = destination
        self.trains_on_data = trains_on_data
        self._replies = replies or ["ok"]
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Record the request and return the next canned reply."""
        index = min(len(self.requests), len(self._replies) - 1)
        self.requests.append(request)
        return LLMResponse(
            text=self._replies[index],
            provider=self.name,
            model=self.model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )
