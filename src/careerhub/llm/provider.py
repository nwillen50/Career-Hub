"""What a provider is: one model reachable one way, local or in the cloud."""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from careerhub.llm.types import LLMRequest, LLMResponse


class Destination(Enum):
    """Where a provider sends the prompt."""

    LOCAL = "local"  # runs on this Mac; the prompt never leaves it
    CLOUD = "cloud"  # sent over the network to a company's servers


class Provider(Protocol):
    """The interface every provider implements (Anthropic, OpenAI, Google, local, test fakes).

    Providers do no privacy checking themselves: the `Router` does, before calling `complete`.
    """

    #: Unique name used in settings and routes, e.g. "anthropic-mid".
    name: str
    #: The model identifier sent to the provider.
    model: str
    destination: Destination
    #: True if the provider may train on API data. Such providers never see personal data.
    trains_on_data: bool

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send the request to the model and return its answer."""
        ...
