from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Company, Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Destination, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.fake import FakeProvider
from careerhub.llm.validated import ValidationFailed
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.jobs.filters import assess_filters
from careerhub.modules.jobs.scoring import (
    PROMPT_VERSION,
    ScoreAnswer,
    ScoringError,
    build_request,
    combine,
    ranked_jobs,
    score_job,
)
from careerhub.profile.models import Education, HardConstraints, ProfileDoc
from careerhub.profile.rubric import Criterion, Rubric

RUBRIC = Rubric(
    version=3,
    criteria=[
        Criterion(name="field", weight=3, description="Right field"),
        Criterion(name="level", weight=1, description="Right level"),
    ],
)
DOC = ProfileDoc(
    name="Jane Sample",
    education=[Education(school="Example University", degree="BS CS")],
    skills=["Python"],
    interests=["ml"],
    about="Likes small teams.",
)


def answer(field: int = 10, level: int = 0) -> str:
    return json.dumps(
        {
            "criteria": [
                {"name": "Field", "score": field, "reason": "r"},
                {"name": "level", "score": level, "reason": "r"},
            ],
            "explanation": "Strong field match.",
        }
    )


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def add_job(session: Session, ext: str, title: str, passed: bool = True) -> Job:
    source = session.scalars(select(Source)).first()
    if source is None:
        source = Source(kind="greenhouse", name="s", url="u", status="approved")
        session.add(source)
        session.commit()
    store_jobs(
        session,
        source,
        [NormalizedJob("Acme", ext, title, "u", "Boston, MA", "Text")]
        + [
            NormalizedJob("Acme", j.external_id, j.title, "u", "Boston, MA", "Text")
            for j in session.scalars(select(Job))
        ],
    )
    job = session.scalars(select(Job).where(Job.external_id == ext)).one()
    assess_filters(session, job, HardConstraints(excluded_title_words=[] if passed else ["bad"]))
    return job


def test_the_code_computes_the_weighted_score() -> None:
    assert combine(RUBRIC, ScoreAnswer.model_validate_json(answer(10, 0))) == 75.0
    assert combine(RUBRIC, ScoreAnswer.model_validate_json(answer(10, 10))) == 100.0
    assert combine(RUBRIC, ScoreAnswer.model_validate_json(answer(0, 0))) == 0.0


def test_missing_duplicate_or_unknown_criteria_are_errors() -> None:
    missing = ScoreAnswer.model_validate(
        {"criteria": [{"name": "field", "score": 5}], "explanation": "x"}
    )
    with pytest.raises(ScoringError):
        combine(RUBRIC, missing)
    extra = ScoreAnswer.model_validate(
        {
            "criteria": [{"name": "field", "score": 5}, {"name": "field", "score": 5}],
            "explanation": "x",
        }
    )
    with pytest.raises(ScoringError, match="twice"):
        combine(RUBRIC, extra)


def test_score_range_is_validated() -> None:
    with pytest.raises(ValueError):
        ScoreAnswer.model_validate({"criteria": [{"name": "a", "score": 11}], "explanation": "x"})


def test_a_job_is_scored_and_saved_with_its_provenance(session: Session) -> None:
    job = add_job(session, "1", "Engineer")
    router = Router([FakeProvider("m", replies=[answer()])])
    saved = score_job(session, router, job, DOC, RUBRIC, ["m"])
    assert saved is not None and saved.fit_score == 75.0
    assert saved.explanation == "Strong field match." and saved.model_used == "m:m-model"
    assert (saved.prompt_version, saved.rubric_version) == (PROMPT_VERSION, 3)
    assert saved.criteria_scores[0]["name"] == "Field"


def test_a_scored_job_is_skipped_until_the_rubric_version_changes(session: Session) -> None:
    job = add_job(session, "1", "Engineer")
    provider = FakeProvider("m", replies=[answer()])
    router = Router([provider])
    score_job(session, router, job, DOC, RUBRIC, ["m"])
    assert score_job(session, router, job, DOC, RUBRIC, ["m"]) is None
    newer = Rubric(version=4, criteria=RUBRIC.criteria)
    assert score_job(session, router, job, DOC, newer, ["m"]) is not None
    assert len(provider.requests) == 2


def test_filtered_jobs_and_unfiltered_jobs_are_not_scored(session: Session) -> None:
    rejected = add_job(session, "1", "bad Engineer", passed=False)
    router = Router([FakeProvider("m", replies=[answer()])])
    assert score_job(session, router, rejected, DOC, RUBRIC, ["m"]) is None
    loose = Job(title="x")  # no assessment row at all
    loose.id = 999
    assert score_job(session, router, loose, DOC, RUBRIC, ["m"]) is None


def test_the_prompt_is_personal_tier_and_leaves_out_the_name(session: Session) -> None:
    request = build_request(add_job(session, "1", "Engineer"), DOC, RUBRIC)
    assert request.tier is PrivacyTier.PERSONAL
    assert "Jane" not in request.prompt and "Python" in request.prompt and "field" in request.prompt


def test_a_training_cloud_provider_cannot_score(session: Session) -> None:
    job = add_job(session, "1", "Engineer")
    cloud = FakeProvider("c", Destination.CLOUD, trains_on_data=True, replies=[answer()])
    with pytest.raises(PrivacyViolation):
        score_job(session, Router([cloud]), job, DOC, RUBRIC, ["c"])
    assert cloud.requests == []


def test_an_unusable_answer_raises_without_saving(session: Session) -> None:
    job = add_job(session, "1", "Engineer")
    with pytest.raises(ValidationFailed):
        score_job(session, Router([FakeProvider("m", replies=["nope"])]), job, DOC, RUBRIC, ["m"])
    assert session.scalars(select(Assessment)).one().fit_score is None


def test_ranking_orders_by_score_and_hides_closed_duplicate_and_filtered_jobs(
    session: Session,
) -> None:
    first = add_job(session, "1", "Low")
    second = add_job(session, "2", "High")
    third = add_job(session, "3", "Closed")
    router = Router([FakeProvider("m", replies=[answer(2), answer(9), answer(10)])])
    for job in (first, second, third):
        score_job(session, router, job, DOC, RUBRIC, ["m"])
    third.status = "closed"
    session.commit()
    assert [job.title for job, _ in ranked_jobs(session)] == ["High", "Low"]


def test_rubric_text_is_redacted_before_it_reaches_a_model() -> None:
    """CH-133: an email, link, or the user's name typed into rubric.yaml must not be sent."""
    rubric = Rubric(
        version=1,
        criteria=[
            Criterion(
                name="field",
                weight=1,
                description="Jane Sample wants roles; write jane@example.com or https://x.io/jane",
            )
        ],
    )
    job = Job(title="Engineer", company=Company(name="Acme"), raw_text="x")
    prompt = build_request(job, DOC, rubric).prompt
    assert "jane@example.com" not in prompt
    assert "https://x.io" not in prompt
    assert "Jane Sample" not in prompt


def test_a_redacted_criterion_name_still_scores(session: Session) -> None:
    """CH-133 review: the model echoes the redacted name, and it must still match."""
    rubric = Rubric(
        version=1,
        criteria=[
            Criterion(name="Fit with jane@example.com's lab", weight=1, description="Likes it"),
            Criterion(name="level", weight=1, description="Call 617-555-0100 to ask"),
        ],
    )
    job = add_job(session, "1", "Engineer")
    prompt = build_request(job, DOC, rubric).prompt
    assert "jane@example.com" not in prompt and "617-555-0100" not in prompt
    reply = json.dumps(
        {
            "criteria": [
                {"name": "Fit with [email]'s lab", "score": 8, "reason": "r"},
                {"name": "level", "score": 4, "reason": "r"},
            ],
            "explanation": "Fine.",
        }
    )
    router = Router([FakeProvider("m", replies=[reply])])
    saved = score_job(session, router, job, DOC, rubric, ["m"])
    assert saved is not None and saved.fit_score == 60.0
