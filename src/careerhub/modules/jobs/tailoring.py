"""Tailoring notes: how to present oneself for a shortlisted job (build step 8).

A model reads the user's profile and resume text and the posting, and writes three short lists:
what to lead with, gaps to address, and suggested resume edits. These are **notes for the user**:
nothing here edits the resume, writes an application, or sends anything.

Privacy: calls are tier PERSONAL. Before anything goes to a model, email addresses, phone numbers,
web addresses, and the user's name are removed from the resume and the profile text (the model
doesn't need them). The call is not `essential`, so the monthly budget pauses it first. Results
are saved on the job's `Assessment` with the prompt version and a hash of the resume, so they are
redone only when the prompt or the resume changes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Job
from careerhub.llm import LLMRequest, PrivacyTier, Router
from careerhub.llm.validated import complete_validated_detailed
from careerhub.modules.jobs.scoring import profile_text
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import accepted_facts
from careerhub.profile.models import ProfileDoc
from careerhub.profile.redact import personal_text
from careerhub.prompts import load_prompt, render

PROMPT_NAME = "tailor_job"
PROMPT_VERSION = 2
MAX_RESUME_CHARS = 8_000
MAX_POSTING_CHARS = 6_000


class TailoringNotes(BaseModel):
    """The model's advice: three short lists."""

    model_config = ConfigDict(extra="ignore")

    lead_with: list[str] = Field(default_factory=list, max_length=5)
    gaps: list[str] = Field(default_factory=list, max_length=5)
    resume_suggestions: list[str] = Field(default_factory=list, max_length=5)


def resume_for_model(resume_text: str, name: str = "", aliases: Sequence[str] = ()) -> str:
    """Return the resume text with contact details and the name removed, cut to length.

    `resume_text` is plain text from any format (see `careerhub.profile.documents`).
    """
    return personal_text(resume_text, name, aliases)[:MAX_RESUME_CHARS]


def preferred_skills(job: Job) -> list[str]:
    """Return the nice-to-have skills extracted from the posting (strings only)."""
    fields = (job.structured_fields or {}).get("fields") or {}
    skills = fields.get("preferred_skills") or []
    return [str(skill) for skill in skills]


def build_request(
    job: Job, doc: ProfileDoc, resume_text: str, facts: ProfileFacts | None = None
) -> LLMRequest:
    """Build the (PERSONAL tier, non-essential) request for one job."""
    prompt = render(
        load_prompt(PROMPT_NAME, PROMPT_VERSION),
        profile=profile_text(doc, facts),
        resume=resume_text,
        company=job.company.name,
        title=job.title,
        preferred=", ".join(preferred_skills(job)) or "none listed",
        posting=(job.raw_text or "(no description)")[:MAX_POSTING_CHARS],
    )
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PERSONAL, max_tokens=900, essential=False)


def tailor_job(
    session: Session,
    router: Router,
    job: Job,
    doc: ProfileDoc,
    resume_text: str,
    routes: Sequence[str],
) -> TailoringNotes | None:
    """Write and save tailoring notes. Returns None if the job isn't scored or is up to date."""
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    if assessment is None or assessment.fit_score is None:
        return None  # only scored (shortlisted) jobs get notes
    resume = resume_for_model(resume_text, doc.name, doc.aliases)
    facts = accepted_facts(session)
    about_me = profile_text(doc, facts)
    wanted = ", ".join(preferred_skills(job))
    material = (
        f"{resume}\n{about_me}\n{job.company.name}\n{job.title}\n{job.raw_text or ''}\n{wanted}"
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    saved = assessment.tailoring or {}
    if saved.get("prompt_version") == PROMPT_VERSION and saved.get("resume_sha256") == digest:
        return None
    notes, response = complete_validated_detailed(
        router, build_request(job, doc, resume, facts), TailoringNotes, routes
    )
    assessment.tailoring = {
        "notes": notes.model_dump(),
        "prompt_version": PROMPT_VERSION,
        "resume_sha256": digest,
        "model": f"{response.provider}:{response.model}",
    }
    session.commit()
    return notes
