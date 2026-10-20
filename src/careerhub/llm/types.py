"""The request and response types every provider shares."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class PrivacyTier(IntEnum):
    """How sensitive the most sensitive data in a call is. Higher means more restricted.

    - PUBLIC: job postings, company information. Any model may see it.
    - PERSONAL: resume, profile, preferences, transcripts. Cloud or local.
    - SENSITIVE: email bodies, calendar details, contacts' personal details. Local models only.
    """

    PUBLIC = 0
    PERSONAL = 1
    SENSITIVE = 2


@dataclass(frozen=True)
class LLMRequest:
    """One call to a model.

    `tier` is required and has no default: the caller must say what data the prompt contains.
    """

    prompt: str
    tier: PrivacyTier
    system: str | None = None
    max_tokens: int = 1024


@dataclass(frozen=True)
class LLMResponse:
    """A model's answer, with the facts later steps need for cost tracking."""

    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
