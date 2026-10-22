"""Tests for the shared Personal-tier text preparation (CH-107)."""

from __future__ import annotations

import pytest

from careerhub.core.models import Job
from careerhub.modules.jobs import scoring, tailoring
from careerhub.profile.models import ProfileDoc
from careerhub.profile.redact import personal_text, remove_name
from careerhub.profile.rubric import Criterion, Rubric

RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
ABOUT = (
    "Reach me at me@example.com, 617-555-0142 or github.com/someone. "
    "I'm Nicholas Willen, also Nick, handle nwillen50. Annual planning is fun."
)
DOC = ProfileDoc(
    name="Nicholas Willen", aliases=["Nick", "nwillen50"], skills=["Python"], about=ABOUT
)
LEAKS = ["me@example.com", "555-0142", "github.com", "Nicholas", "Willen", "Nick", "nwillen50"]


def make_job() -> Job:
    job = Job(title="Engineer", raw_text="text", structured_fields={})
    job.company = type("C", (), {"name": "Acme"})()  # type: ignore[assignment]
    return job


def test_names_match_whole_words_only() -> None:
    assert remove_name("Annual planning, Ann said", "Ann Lee") == "Annual planning, [name] said"


def test_nicknames_and_handles_are_removed_with_aliases() -> None:
    text = "Nick Willen, Nicholas Willen, nwillen50"
    assert remove_name(text, "Nicholas Willen", ["Nick", "nwillen50"]) == "[name], [name], [name]"


def test_short_or_blank_aliases_are_ignored() -> None:
    assert remove_name("Al went to a mall", "Al Bo", ["", " ", "al"]) == "Al went to a mall"


def test_personal_text_removes_contacts_and_the_name() -> None:
    out = personal_text(ABOUT, DOC.name, DOC.aliases)
    assert not [leak for leak in LEAKS if leak in out]
    assert "Annual planning" in out


def test_scoring_prompt_has_no_contacts_name_or_aliases() -> None:
    prompt = scoring.build_request(make_job(), DOC, RUBRIC).prompt
    assert not [leak for leak in LEAKS if leak in prompt]
    assert "Annual planning" in prompt


def test_tailoring_prompt_has_no_contacts_name_or_aliases() -> None:
    resume = tailoring.resume_for_model(ABOUT, DOC.name, DOC.aliases)
    prompt = tailoring.build_request(make_job(), DOC, resume).prompt
    assert not [leak for leak in LEAKS if leak in prompt]


@pytest.mark.parametrize("name", ["", "  "])
def test_a_blank_name_still_redacts_contacts(name: str) -> None:
    assert personal_text("mail a@b.co", name) == "mail [email]"
