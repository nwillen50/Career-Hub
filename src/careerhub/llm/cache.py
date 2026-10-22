"""The response cache: a repeated call is answered from the encrypted database, free of charge.

A call is identified by provider, model, and a hash of everything that shapes the answer (system
text, prompt, token limit). The privacy tier is *not* part of the key: the router checks the tier
before it looks in the cache, so a cache hit can never hand data to a provider that the tier
forbids. Only the hash is stored, never the prompt.

Entries expire `ttl_days` after they were last used (using one resets the clock), so the cache
doesn't grow forever or keep model output about the user longer than needed. An expired entry is a
miss; `prune` deletes expired entries and `clear` deletes everything.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.models import LLMCacheEntry, utcnow
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

    def __init__(
        self,
        sessions: sessionmaker[Session],
        ttl_days: int = 30,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        """Use `sessions` (from `careerhub.core.storage.session_factory`) for every lookup.

        Entries expire `ttl_days` after last use; `now` is the clock (tests pass a fake one).
        """
        self._sessions = sessions
        self._ttl = timedelta(days=ttl_days)
        self._now = now

    def get(self, provider: Provider, request: LLMRequest) -> LLMResponse | None:
        """Return the saved answer for this call, or None. The result has `cached=True`."""
        with self._sessions() as session:
            entry = self._find(session, provider, request)
            if entry is None or entry.last_used_at <= self._now() - self._ttl:
                return None
            entry.last_used_at = self._now()  # a hit resets the clock
            session.commit()
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
            entry.last_used_at = self._now()
            session.commit()

    def prune(self) -> int:
        """Delete expired entries and return how many were removed (a count, never content)."""
        with self._sessions() as session:
            result = session.execute(
                delete(LLMCacheEntry).where(LLMCacheEntry.last_used_at <= self._now() - self._ttl)
            )
            session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def clear(self) -> int:
        """Delete every entry and return how many were removed."""
        with self._sessions() as session:
            result = session.execute(delete(LLMCacheEntry))
            session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

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
