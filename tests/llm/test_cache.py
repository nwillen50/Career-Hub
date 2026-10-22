from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from careerhub.core.migrate import migrate
from careerhub.core.models import LLMCacheEntry
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Destination, LLMRequest, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.cache import ResponseCache, prompt_hash
from careerhub.llm.fake import FakeProvider


@pytest.fixture
def sessions(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)


def ask(prompt: str = "sample prompt", tier: PrivacyTier = PrivacyTier.PUBLIC) -> LLMRequest:
    return LLMRequest(prompt=prompt, tier=tier)


def test_a_repeated_call_is_answered_without_calling_the_provider(sessions) -> None:  # type: ignore[no-untyped-def]
    fake = FakeProvider(replies=["first", "second"])
    router = Router([fake], cache=ResponseCache(sessions))
    first = router.complete(ask(), "fake")
    again = router.complete(ask(), "fake")
    assert (first.text, first.cached) == ("first", False)
    assert (again.text, again.cached) == ("first", True)
    assert len(fake.requests) == 1


def test_a_different_prompt_or_limit_is_a_different_call(sessions) -> None:  # type: ignore[no-untyped-def]
    fake = FakeProvider()
    router = Router([fake], cache=ResponseCache(sessions))
    router.complete(ask("a"), "fake")
    router.complete(ask("b"), "fake")
    router.complete(LLMRequest(prompt="a", tier=PrivacyTier.PUBLIC, max_tokens=5), "fake")
    assert len(fake.requests) == 3


def test_the_cache_is_per_provider(sessions) -> None:  # type: ignore[no-untyped-def]
    one, two = FakeProvider("one"), FakeProvider("two")
    router = Router([one, two], cache=ResponseCache(sessions))
    router.complete(ask(), "one")
    router.complete(ask(), "two")
    assert len(one.requests) == len(two.requests) == 1


def test_the_privacy_check_runs_before_the_cache(sessions) -> None:  # type: ignore[no-untyped-def]
    """A Sensitive answer cached from a local model is never served as a cloud model's."""
    cache = ResponseCache(sessions)
    local = FakeProvider("local")
    cloud = FakeProvider("cloud", Destination.CLOUD)
    router = Router([local, cloud], cache=cache)
    request = ask(tier=PrivacyTier.SENSITIVE)
    router.complete(request, "local")
    with pytest.raises(PrivacyViolation):
        router.complete(request, "cloud")
    assert cache.get(cloud, request) is None


def test_the_prompt_is_never_stored(sessions) -> None:  # type: ignore[no-untyped-def]
    router = Router([FakeProvider()], cache=ResponseCache(sessions))
    router.complete(ask("my secret resume text"), "fake")
    with sessions() as session:
        entry = session.scalars(select(LLMCacheEntry)).one()
        assert "secret" not in repr(vars(entry))
        assert entry.prompt_hash == prompt_hash(ask("my secret resume text"))


def test_saving_the_same_call_twice_keeps_one_row(sessions) -> None:  # type: ignore[no-untyped-def]
    cache = ResponseCache(sessions)
    fake = FakeProvider()
    request = ask()
    response = fake.complete(request)
    cache.put(fake, request, response)
    cache.put(fake, request, response)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(LLMCacheEntry)) == 1


def test_hash_parts_cannot_be_confused() -> None:
    a = LLMRequest(prompt="bc", tier=PrivacyTier.PUBLIC, system="a")
    b = LLMRequest(prompt="c", tier=PrivacyTier.PUBLIC, system="ab")
    assert prompt_hash(a) != prompt_hash(b)


def test_a_router_without_a_cache_calls_every_time() -> None:
    fake = FakeProvider()
    router = Router([fake])
    router.complete(ask(), "fake")
    router.complete(ask(), "fake")
    assert len(fake.requests) == 2


class Clock:
    """A clock the tests move by hand."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: float) -> None:
        self.now += timedelta(days=days)


def cached_router(sessions, clock: Clock, ttl_days: int = 30):  # type: ignore[no-untyped-def]
    fake = FakeProvider(replies=["first", "second", "third"])
    cache = ResponseCache(sessions, ttl_days, clock)
    return fake, cache, Router([fake], cache=cache)


def test_an_expired_entry_is_a_miss(sessions) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    fake, _, router = cached_router(sessions, clock)
    router.complete(ask(), "fake")
    clock.advance(29)
    assert router.complete(ask(), "fake").cached
    clock.advance(31)  # more than 30 days since the last use
    assert router.complete(ask(), "fake").text == "second"
    assert len(fake.requests) == 2


def test_expiry_is_sliding_because_each_use_resets_the_clock(sessions) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    _, _, router = cached_router(sessions, clock)
    router.complete(ask(), "fake")
    for _ in range(4):
        clock.advance(20)  # 80 days in all, but never 30 unused
        assert router.complete(ask(), "fake").cached


def test_prune_removes_only_expired_entries_and_counts_them(sessions) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    _, cache, router = cached_router(sessions, clock)
    router.complete(ask("old"), "fake")
    clock.advance(40)
    router.complete(ask("new"), "fake")
    assert cache.prune() == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(LLMCacheEntry)) == 1
    assert cache.prune() == 0


def test_clear_removes_everything(sessions) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    _, cache, router = cached_router(sessions, clock)
    router.complete(ask("a"), "fake")
    router.complete(ask("b"), "fake")
    assert cache.clear() == 2
    assert cache.clear() == 0
