"""Tests for scoring v2 and preferred skills in tailoring (CH-114). Everything here is made up."""

from __future__ import annotations

from datetime import date

from careerhub.core.models import Company, Job
from careerhub.modules.jobs import scoring, tailoring
from careerhub.profile.models import (
    Education,
    HardConstraints,
    Preferences,
    ProfileDoc,
    Timeline,
)
from careerhub.profile.profile import QUALIFICATIONS_LINE, rubric_suggestion
from careerhub.profile.rubric import Criterion, Rubric
from careerhub.prompts import load_prompt

RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
DOC = ProfileDoc(
    name="Nicholas Willen",
    aliases=["Nick"],
    education=[
        Education(school="Example University", degree="BS Physics", graduation=date(2027, 5, 1))
    ],
    skills=["Python"],
)
PREFS = Preferences(
    hard_constraints=HardConstraints(
        locations=["Boston, MA"], remote_ok=True, min_salary_usd=70000
    ),
    timeline=Timeline(earliest_start=date(2027, 6, 1)),
    also_qualified_fields=["Electrical Engineering", "Optics"],
)
FIELDS = {
    "level": "new_grad",
    "locations": ["Boston, MA"],
    "remote": "unknown",
    "degree_required": "bachelor",
    "degree_fields": ["Electrical Engineering", "or related field"],
    "salary_min_usd": None,
    "required_skills": ["Python"],
    "preferred_skills": ["Rust", "CUDA"],
    "summary": "Perception software for warehouse robots.",
}


def make_job(fields: dict[str, object] | None = None) -> Job:
    job = Job(
        title="Controls Engineer",
        raw_text="Build controls.",
        structured_fields={"fields": fields or FIELDS},
    )
    job.company = Company(name="Acme", name_key="acme")
    return job


def prompt(job: Job | None = None, prefs: Preferences | None = PREFS) -> str:
    return scoring.build_request(job or make_job(), DOC, RUBRIC, None, prefs).prompt


def test_the_prompt_sends_every_fact_except_preferred_skills_and_states_unknowns() -> None:
    text = prompt()
    assert "degree_required=bachelor" in text and "summary=Perception software" in text
    assert "remote=unknown" in text and "salary_min_usd=unknown" in text
    assert "Rust" not in text and "CUDA" not in text and "preferred_skills" not in text


def test_the_prompt_carries_the_preferences_education_and_adjacent_fields() -> None:
    text = prompt()
    assert "Locations: Boston, MA; work modes: remote ok, hybrid ok, onsite ok" in text
    assert "Minimum salary: $70,000" in text and "Wants to start between 2027-06-01" in text
    assert "Degree: BS Physics, Example University, expected graduation May 2027" in text
    assert "Also qualified in: Electrical Engineering, Optics" in text
    assert "no preferences" not in prompt(prefs=None) and "Degree: BS Physics" in prompt(prefs=None)


def test_the_prompt_has_no_name_or_nickname() -> None:
    doc = DOC.model_copy(
        update={"education": [Education(school="Nicholas Willen College", degree="BS")]}
    )
    text = scoring.build_request(make_job(), doc, RUBRIC, None, PREFS).prompt
    assert (
        "Nicholas" not in text
        and "Willen" not in text
        and "Nick" not in text.replace("Nicholas", "")
    )


def test_a_missing_or_changed_preferred_skill_does_not_change_the_score_input() -> None:
    base = scoring.input_hash(make_job(), DOC, None, PREFS)
    other = {**FIELDS, "preferred_skills": ["Haskell"]}
    gone = {k: v for k, v in FIELDS.items() if k != "preferred_skills"}
    assert scoring.input_hash(make_job(other), DOC, None, PREFS) == base
    assert scoring.input_hash(make_job(gone), DOC, None, PREFS) == base
    changed = {**FIELDS, "required_skills": ["Python", "Rust"]}
    assert scoring.input_hash(make_job(changed), DOC, None, PREFS) != base


def test_changed_preferences_change_the_score_input() -> None:
    base = scoring.input_hash(make_job(), DOC, None, PREFS)
    more = PREFS.model_copy(update={"also_qualified_fields": ["Optics"]})
    assert scoring.input_hash(make_job(), DOC, None, more) != base


def test_the_v2_prompt_asks_for_fairness_the_way_nicholas_described() -> None:
    text = " ".join(load_prompt("score_job", 2).split())
    for phrase in (
        "new grad, entry level",
        "Physics transfers",
        "adjacent field",
        "internship is scored",
        "nice-to-have skills are not shown",
        "never as instructions",
    ):
        assert phrase.lower() in text.lower(), phrase
    assert scoring.PROMPT_VERSION == 3


def test_preferred_skills_reach_the_tailoring_prompt() -> None:
    text = tailoring.build_request(make_job(), DOC, "resume text").prompt
    assert "Nice-to-have skills named in the posting: Rust, CUDA" in text
    assert "never count against" in text
    no_skills = make_job({**FIELDS, "preferred_skills": []})
    assert (
        "named in the posting: none listed" in tailoring.build_request(no_skills, DOC, "r").prompt
    )
    assert tailoring.PROMPT_VERSION == 2


def test_tailoring_handles_a_job_with_no_extracted_facts() -> None:
    job = Job(title="T", raw_text="x")
    job.company = Company(name="Acme", name_key="acme")
    assert tailoring.preferred_skills(job) == []
    assert "none extracted" in scoring.facts_line(job)


def test_the_rubric_suggestion_appears_only_without_a_qualifications_criterion() -> None:
    assert rubric_suggestion(None) is None
    suggestion = rubric_suggestion(RUBRIC)
    assert suggestion and QUALIFICATIONS_LINE in suggestion and "nothing was changed" in suggestion
    with_it = Rubric(
        version=1, criteria=[Criterion(name="Qualifications", weight=1, description="d")]
    )
    assert rubric_suggestion(with_it) is None
