"""The response cache: a repeated call is answered from the encrypted database, free of charge.

A call is identified by provider, model, and a hash of everything that shapes the answer (system
text, prompt, token limit). The privacy tier is *not* part of the key: the router checks the tier
before it looks in the cache, so a cache hit can never hand data to a provider that the tier
forbids. Only the hash is stored, never the prompt.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.models import LLMCacheEntry
from careerhub.llm.provider import Provider
from careerhub.llm.types import LLMRequest, LLMResponse


def prompt_hash(request: LLMRequest) -> str:
    """Return a SHA-256 hex digest of the parts of `request` that change the model's answer."""
    # JSON-free but unambiguous: each part is length-prefixed so "ab"+"c" differs from "a"+"bc".
    parts = [request.system or "", request.prompt, str(request.max_tokens)]
    data = b"".join(f"{len(p)}:".encode() + p.encode() for p in parts)
    return hashlib.sha256(data).hexdigest()


class ResponseCache:
    """Reads and writes cached answers in the encrypted database."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        """Use `sessions` (from `careerhub.core.storage.session_factory`) for every lookup."""
        self._sessions = sessions

    def get(self, provider: Provider, request: LLMRequest) -> LLMResponse | None:
        """Return the saved answer for this call, or None. The result has `cached=True`."""
        with self._sessions() as session:
            entry = self._find(session, provider, request)
            if entry is None:
                return None
            return LLMResponse(
                text=entry.response_text,
                provider=entry.provider,
                model=entry.model,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                cached=True,
            )

    def put(self, provider: Provider, request: LLMRequest, response: LLMResponse) -> None:
        """Save `response` for this call, replacing any older answer for the same call."""
        with self._sessions() as session:
            entry = self._find(session, provider, request)
            if entry is None:
                entry = LLMCacheEntry(
                    provider=provider.name,
                    model=provider.model,
                    prompt_hash=prompt_hash(request),
                    response_text=response.text,
                )
                session.add(entry)
            entry.response_text = response.text
            entry.input_tokens = response.input_tokens
            entry.output_tokens = response.output_tokens
            session.commit()

    @staticmethod
    def _find(session: Session, provider: Provider, request: LLMRequest) -> LLMCacheEntry | None:
        """Return the row for this call, if one exists."""
        return session.scalars(
            select(LLMCacheEntry).where(
                LLMCacheEntry.provider == provider.name,
                LLMCacheEntry.model == provider.model,
                LLMCacheEntry.prompt_hash == prompt_hash(request),
            )
        ).first()
