"""Score a job against the rubric and save the result (build step 6).

The model rates each rubric criterion 0 to 10 and writes an explanation; the **code** combines the
ratings into the fit score (a weighted average scaled to 0-100), so the arithmetic is never the
model's job. Calls are tier PERSONAL (the prompt contains the user's profile, without their name),
so they only reach providers the privacy rules allow. Each saved score records the model, prompt
version, and rubric version, and a job already scored with the same versions is skipped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Job
from careerhub.llm import LLMRequest, PrivacyTier, Router
from careerhub.llm.validated import complete_validated_detailed
from careerhub.modules.jobs.store import only_visible_copies
from careerhub.profile.models import ProfileDoc
from careerhub.profile.redact import personal_text
from careerhub.profile.rubric import Rubric
from careerhub.prompts import load_prompt, render

PROMPT_NAME = "score_job"
PROMPT_VERSION = 1
MAX_POSTING_CHARS = 6_000


class ScoringError(ValueError):
    """The model's answer didn't cover the rubric correctly. The message has no model text."""

    safe_message = True


class CriterionScore(BaseModel):
    """The model's rating of one criterion."""

    model_config = ConfigDict(extra="ignore")

    name: str
    score: int = Field(ge=0, le=10)
    reason: str = ""


class ScoreAnswer(BaseModel):
    """What the model must return."""

    model_config = ConfigDict(extra="ignore")

    criteria: list[CriterionScore]
    explanation: str = Field(min_length=1, max_length=1500)


def profile_text(doc: ProfileDoc) -> str:
    """Describe the person for the prompt, with contact details, name, and aliases removed.

    The name is never listed, and anything the user wrote in `about` goes through `personal_text`,
    so a link, email, phone number, or the name itself can't reach a cloud model.
    """
    lines = [f"Education: {e.degree}, {e.school}" for e in doc.education]
    lines.append(f"Skills: {', '.join(doc.skills) or 'not listed'}")
    lines.append(f"Interests: {', '.join(doc.interests) or 'not listed'}")
    if doc.about:
        lines.append(f"About: {doc.about[:1500]}")
    return personal_text("\n".join(lines), doc.name, doc.aliases)


def rubric_text(rubric: Rubric) -> str:
    """List the criteria with their weights and descriptions."""
    return "\n".join(f"- {c.name} (weight {c.weight:g}): {c.description}" for c in rubric.criteria)


def combine(rubric: Rubric, answer: ScoreAnswer) -> float:
    """Return the weighted fit score, 0 to 100.

    Raises `ScoringError` unless every criterion is scored exactly once.
    """
    scores: dict[str, int] = {}
    for item in answer.criteria:
        key = item.name.strip().lower()
        if key in scores:
            raise ScoringError(f"criterion {item.name!r} was scored twice")
        scores[key] = item.score
    wanted = {c.name.strip().lower() for c in rubric.criteria}
    if set(scores) != wanted:
        raise ScoringError("the answer did not score exactly the rubric's criteria")
    total = sum(c.weight for c in rubric.criteria)
    weighted = sum(c.weight * scores[c.name.strip().lower()] for c in rubric.criteria)
    return round(weighted / total * 10, 1)


def input_hash(job: Job, doc: ProfileDoc) -> str:
    """Hash what a score depends on besides the prompt and rubric: profile, posting, and facts."""
    material = json.dumps(
        [profile_text(doc), job.title, job.raw_text or "", job.structured_fields.get("fields", {})],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def build_request(job: Job, doc: ProfileDoc, rubric: Rubric) -> LLMRequest:
    """Build the (PERSONAL tier) request for one job."""
    fields = job.structured_fields.get("fields", {})
    prompt = render(
        load_prompt(PROMPT_NAME, PROMPT_VERSION),
        profile=profile_text(doc),
        rubric=rubric_text(rubric),
        company=job.company.name,
        title=job.title,
        facts=", ".join(f"{k}={v}" for k, v in fields.items() if v not in (None, [], "unknown"))
        or "none extracted",
        posting=(job.raw_text or "(no description)")[:MAX_POSTING_CHARS],
    )
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PERSONAL, max_tokens=900, essential=True)


def score_job(
    session: Session,
    router: Router,
    job: Job,
    doc: ProfileDoc,
    rubric: Rubric,
    routes: Sequence[str],
) -> Assessment | None:
    """Score `job` and save it. Returns None if it failed a filter or is already scored."""
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    if assessment is None or not assessment.filter_passed:
        return None  # run the filters first; filtered jobs are not scored
    digest = input_hash(job, doc)
    if (
        assessment.fit_score is not None
        and assessment.prompt_version == PROMPT_VERSION
        and assessment.rubric_version == rubric.version
        and assessment.input_hash == digest
    ):
        return None
    answer, response = complete_validated_detailed(
        router,
        build_request(job, doc, rubric),
        ScoreAnswer,
        routes,
        check=lambda parsed: combine(rubric, parsed),
    )
    assessment.fit_score = combine(rubric, answer)
    assessment.criteria_scores = [item.model_dump() for item in answer.criteria]
    assessment.explanation = answer.explanation
    assessment.model_used = f"{response.provider}:{response.model}"
    assessment.prompt_version = PROMPT_VERSION
    assessment.rubric_version = rubric.version
    assessment.input_hash = digest
    session.commit()
    return assessment


def ranked_jobs(session: Session, limit: int = 20) -> list[tuple[Job, Assessment]]:
    """Return open, original (not duplicate) jobs that passed the filters, best score first."""
    query = (
        select(Job, Assessment)
        .join(Assessment, Assessment.job_id == Job.id)
        .where(
            Assessment.filter_passed.is_(True),
            Assessment.fit_score.is_not(None),
            Job.status != "closed",
        )
    )
    query = only_visible_copies(query).order_by(Assessment.fit_score.desc(), Job.id).limit(limit)
    return [(job, assessment) for job, assessment in session.execute(query)]
