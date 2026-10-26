from __future__ import annotations

import pytest

from careerhub.llm import (
    Destination,
    LLMRequest,
    LLMResponse,
    PrivacyTier,
    PrivacyViolation,
    Router,
    UnknownProvider,
)
from careerhub.llm.fake import FakeProvider
from careerhub.llm.providers import ProviderError
from careerhub.llm.router import BENCH_AFTER, RATE_LIMIT_WAIT_SECONDS

LOCAL = Destination.LOCAL
CLOUD = Destination.CLOUD


def request(tier: PrivacyTier) -> LLMRequest:
    return LLMRequest(prompt="sample prompt", tier=tier)


def test_a_sensitive_call_to_a_cloud_model_is_refused() -> None:
    """The step 2 acceptance test (PRD): Sensitive data never reaches a cloud model."""
    cloud = FakeProvider("cloud", CLOUD)
    router = Router([cloud])
    with pytest.raises(PrivacyViolation):
        router.complete(request(PrivacyTier.SENSITIVE), "cloud")
    assert cloud.requests == []  # refused before the provider was ever called


@pytest.mark.parametrize("tier", list(PrivacyTier))
def test_local_models_may_see_every_tier(tier: PrivacyTier) -> None:
    local = FakeProvider("local", LOCAL)
    assert Router([local]).complete(request(tier), "local").text == "ok"


@pytest.mark.parametrize("tier", [PrivacyTier.PUBLIC, PrivacyTier.PERSONAL])
def test_cloud_models_may_see_public_and_personal(tier: PrivacyTier) -> None:
    cloud = FakeProvider("cloud", CLOUD)
    Router([cloud]).complete(request(tier), "cloud")
    assert len(cloud.requests) == 1


def test_a_provider_that_trains_on_data_never_sees_personal_data() -> None:
    trainer = FakeProvider("trainer", CLOUD, trains_on_data=True)
    router = Router([trainer])
    with pytest.raises(PrivacyViolation, match="train"):
        router.complete(request(PrivacyTier.PERSONAL), "trainer")
    router.complete(request(PrivacyTier.PUBLIC), "trainer")  # public is fine
    assert len(trainer.requests) == 1


def test_a_refusal_never_falls_back_to_another_provider() -> None:
    cloud, local = FakeProvider("cloud", CLOUD), FakeProvider("local", LOCAL)
    with pytest.raises(PrivacyViolation):
        Router([cloud, local]).complete(request(PrivacyTier.SENSITIVE), "cloud")
    assert local.requests == []


def test_the_tier_cannot_be_left_out() -> None:
    with pytest.raises(TypeError):
        LLMRequest(prompt="no tier given")  # type: ignore[call-arg]


def test_unknown_and_duplicate_providers_are_errors() -> None:
    router = Router([FakeProvider("a")])
    with pytest.raises(UnknownProvider):
        router.complete(request(PrivacyTier.PUBLIC), "missing")
    with pytest.raises(ValueError, match="named 'a'"):
        Router([FakeProvider("a"), FakeProvider("a")])


def test_fake_provider_replays_scripted_replies_and_records_requests() -> None:
    fake = FakeProvider("f", replies=["one", "two"])
    router = Router([fake])
    texts = [router.complete(request(PrivacyTier.PUBLIC), "f").text for _ in range(3)]
    assert texts == ["one", "two", "two"]
    assert len(fake.requests) == 3


def test_tiers_order_from_public_to_sensitive() -> None:
    assert PrivacyTier.PUBLIC < PrivacyTier.PERSONAL < PrivacyTier.SENSITIVE


class _Flaky(FakeProvider):
    """Fails with the scripted errors first, then answers."""

    def __init__(self, errors: list[ProviderError]) -> None:
        super().__init__("flaky")
        self.errors = errors
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return super().complete(request)


_ASK = LLMRequest(prompt="hi", tier=PrivacyTier.PUBLIC)


def test_a_rate_limited_call_waits_and_retries_once() -> None:
    waits: list[float] = []
    flaky = _Flaky([ProviderError("x", "HTTP 429 (busy)", 429)])
    router = Router([flaky], sleep=waits.append)
    assert router.complete(_ASK, "flaky").text == "ok"
    assert waits == [RATE_LIMIT_WAIT_SECONDS] and flaky.calls == 2


def test_a_provider_failing_three_calls_in_a_row_is_benched_for_the_run() -> None:
    flaky = _Flaky([ProviderError("x", "request failed (ReadTimeout)")] * 10)
    router = Router([flaky], sleep=lambda _: None)
    for _ in range(BENCH_AFTER):
        with pytest.raises(ProviderError):
            router.complete(_ASK, "flaky")
    assert flaky.calls == BENCH_AFTER
    with pytest.raises(ProviderError, match="benched"):
        router.complete(_ASK, "flaky")
    assert flaky.calls == BENCH_AFTER  # not called again


def test_a_success_resets_the_failure_count() -> None:
    flaky = _Flaky([ProviderError("x", "boom")] * 2)
    router = Router([flaky])
    for _ in range(2):
        with pytest.raises(ProviderError):
            router.complete(_ASK, "flaky")
    router.complete(_ASK, "flaky")
    flaky.errors = [ProviderError("x", "boom")] * 2
    for _ in range(2):
        with pytest.raises(ProviderError):
            router.complete(_ASK, "flaky")
    assert router.complete(_ASK, "flaky").text == "ok"
