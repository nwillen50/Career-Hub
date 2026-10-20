from __future__ import annotations

import pytest
from pydantic import BaseModel

from careerhub.llm import Destination, LLMRequest, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.fake import FakeProvider
from careerhub.llm.validated import ValidationFailed, complete_validated


class Posting(BaseModel):
    title: str
    remote: bool


GOOD = '{"title": "Data Analyst", "remote": true}'
REQUEST = LLMRequest(prompt="Extract the posting.", tier=PrivacyTier.PUBLIC)


def test_a_valid_answer_is_returned_as_the_schema() -> None:
    router = Router([FakeProvider("cheap", replies=[GOOD])])
    assert complete_validated(router, REQUEST, Posting, ["cheap"]) == Posting(
        title="Data Analyst", remote=True
    )


def test_a_fenced_answer_is_accepted() -> None:
    router = Router([FakeProvider("cheap", replies=[f"```json\n{GOOD}\n```"])])
    assert complete_validated(router, REQUEST, Posting, ["cheap"]).title == "Data Analyst"


def test_an_invalid_answer_is_retried_once_with_the_problem_stated() -> None:
    cheap = FakeProvider("cheap", replies=['{"title": "x"}', GOOD])
    result = complete_validated(Router([cheap]), REQUEST, Posting, ["cheap"])
    assert result.remote is True
    assert len(cheap.requests) == 2
    assert "remote" in cheap.requests[1].prompt and "invalid" in cheap.requests[1].prompt
    assert cheap.requests[1].tier == PrivacyTier.PUBLIC


def test_after_one_failed_retry_the_next_provider_is_used() -> None:
    cheap = FakeProvider("cheap", replies=["not json"])
    strong = FakeProvider("strong", replies=[GOOD])
    result = complete_validated(Router([cheap, strong]), REQUEST, Posting, ["cheap", "strong"])
    assert result.title == "Data Analyst"
    assert len(cheap.requests) == 2 and len(strong.requests) == 1


def test_when_every_provider_fails_the_error_has_no_model_text() -> None:
    secret = "jane.doe@example.com"
    router = Router([FakeProvider("a", replies=[f'{{"title": "{secret}"}}'])])
    with pytest.raises(ValidationFailed) as raised:
        complete_validated(router, REQUEST, Posting, ["a"])
    assert "remote" in str(raised.value) and secret not in str(raised.value)


def test_escalating_a_sensitive_call_to_the_cloud_is_still_refused() -> None:
    local = FakeProvider("local", Destination.LOCAL, replies=["not json"])
    cloud = FakeProvider("cloud", Destination.CLOUD, replies=[GOOD])
    sensitive = LLMRequest(prompt="Classify this email.", tier=PrivacyTier.SENSITIVE)
    with pytest.raises(PrivacyViolation):
        complete_validated(Router([local, cloud]), sensitive, Posting, ["local", "cloud"])
    assert cloud.requests == []


def test_retries_can_be_turned_off_and_routes_are_required() -> None:
    cheap = FakeProvider("cheap", replies=["bad"])
    with pytest.raises(ValidationFailed):
        complete_validated(Router([cheap]), REQUEST, Posting, ["cheap"], retries=0)
    assert len(cheap.requests) == 1
    with pytest.raises(ValueError):
        complete_validated(Router([cheap]), REQUEST, Posting, [])
