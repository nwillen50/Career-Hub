"""Understand a posting: turn its text into structured fields with a model (build step 5).

The prompt is the versioned file `prompts/extract_job_v1.md`. The model's answer is checked
against `JobFields` by `complete_validated` (retry once, then escalate), so a prompt-injected
posting can at worst skew these fields, not produce anything else. Postings are public data, so
calls are tier PUBLIC and may use any provider. Results are saved in `Job.structured_fields`
with the prompt version, and a posting already extracted with this version is skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from careerhub.core.models import Job, utcnow
from careerhub.llm import LLMRequest, PrivacyTier, Router
from careerhub.llm.validated import complete_validated
from careerhub.prompts import load_prompt, render

PROMPT_NAME = "extract_job"
PROMPT_VERSION = 1
#: Postings are cut to this many characters before they reach the model, to bound cost.
MAX_POSTING_CHARS = 12_000

Level = Literal["internship", "new_grad", "entry", "mid", "senior", "staff_plus", "unknown"]
Remote = Literal["remote", "hybrid", "onsite", "unknown"]
Visa = Literal["yes", "no", "unknown"]


class JobFields(BaseModel):
    """What the model must say about a posting. Unknown facts are `unknown`, null, or empty."""

    model_config = ConfigDict(extra="ignore")

    level: Level = "unknown"
    locations: list[str] = Field(default_factory=list)
    remote: Remote = "unknown"
    visa_sponsorship: Visa = "unknown"
    deadline: date | None = None
    years_experience_min: int | None = Field(default=None, ge=0, le=40)
    salary_min_usd: int | None = Field(default=None, ge=0)
    salary_max_usd: int | None = Field(default=None, ge=0)
    required_skills: list[str] = Field(default_factory=list, max_length=10)


def build_request(job: Job, company_name: str) -> LLMRequest:
    """Build the model request for one posting (tier PUBLIC; essential for the shortlist)."""
    text = (job.raw_text or "")[:MAX_POSTING_CHARS]
    prompt = render(
        load_prompt(PROMPT_NAME, PROMPT_VERSION),
        company=company_name,
        title=job.title,
        location=job.location or "not listed",
        posting=text or "(no description)",
    )
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PUBLIC, max_tokens=700, essential=True)


def extract_fields(
    router: Router, job: Job, routes: Sequence[str], company: str | None = None
) -> JobFields:
    """Ask the model for `job`'s fields. Raises `ValidationFailed` if no route answers validly.

    `company` defaults to the job's company; evals pass it for jobs that aren't saved.
    """
    name = company if company is not None else job.company.name
    return complete_validated(router, build_request(job, name), JobFields, routes=routes)


def extract_and_save(
    session: Session, router: Router, job: Job, routes: Sequence[str]
) -> JobFields | None:
    """Extract and store a job's fields; returns None if this prompt version already did."""
    saved = job.structured_fields
    if saved.get("prompt_version") == PROMPT_VERSION and "fields" in saved:
        return None
    fields = extract_fields(router, job, routes)
    job.structured_fields = {
        "fields": fields.model_dump(mode="json"),
        "prompt_version": PROMPT_VERSION,
        "extracted_at": utcnow().isoformat(),
    }
    session.commit()
    return fields
