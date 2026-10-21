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
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Job
from careerhub.llm import LLMRequest, PrivacyTier, Router
from careerhub.llm.validated import complete_validated_detailed
from careerhub.modules.jobs.scoring import profile_text
from careerhub.profile.models import ProfileDoc
from careerhub.prompts import load_prompt, render

PROMPT_NAME = "tailor_job"
PROMPT_VERSION = 1
MAX_RESUME_CHARS = 8_000
MAX_POSTING_CHARS = 6_000

_COMMENT = re.compile(r"(?<!\\)%.*")
_ENVIRONMENT = re.compile(r"\\(?:begin|end)\{[^}]*\}")
_COMMAND_WITH_ARG = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
_BARE_COMMAND = re.compile(r"\\[a-zA-Z]+\*?")
_ESCAPED = re.compile(r"\\([_&%#$])")
_SCHEME = re.compile(r"\b(?:mailto|tel):", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+")
# A web address with no "https://": a domain ending in a common suffix, with an optional path.
_BARE_DOMAIN = re.compile(
    r"\b(?:[\w-]+\.)+(?:com|org|net|io|dev|edu|me|ai|co)\b(?:/\S*)?", re.IGNORECASE
)
# A phone number: optional country code, then 3-3-4 digits (or 3 digits in parentheses).
_PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
# An international number written with country code and groups, like "+44 20 7946 0958".
_INTERNATIONAL = re.compile(r"(?<!\w)\+\d{1,3}(?:[\s.-]?\d{2,4}){2,4}(?!\d)")


class TailoringNotes(BaseModel):
    """The model's advice: three short lists."""

    model_config = ConfigDict(extra="ignore")

    lead_with: list[str] = Field(default_factory=list, max_length=5)
    gaps: list[str] = Field(default_factory=list, max_length=5)
    resume_suggestions: list[str] = Field(default_factory=list, max_length=5)


def latex_to_text(source: str) -> str:
    """Reduce LaTeX source to readable text: comments, commands, and braces removed."""
    text = _ESCAPED.sub(r"\1", _COMMENT.sub("", source))
    text = _ENVIRONMENT.sub("\n", text)
    for _ in range(5):  # unwrap nested \textbf{\emph{...}}
        text = _COMMAND_WITH_ARG.sub(r"\1", text)
    text = _BARE_COMMAND.sub(" ", text).replace("\\\\", "\n")
    text = re.sub(r"[{}]", "", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def redact_contacts(text: str) -> str:
    """Remove email addresses, web addresses (with or without https://), and phone numbers."""
    text = _SCHEME.sub(" ", text)
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _BARE_DOMAIN.sub("[link]", text)
    return _INTERNATIONAL.sub("[phone]", _PHONE.sub("[phone]", text))


def remove_name(text: str, name: str) -> str:
    """Replace the user's name, and each part of it (3+ letters), with `[name]`."""
    parts = [name, *[part for part in name.split() if len(part) >= 3]]
    for part in sorted(set(parts), key=len, reverse=True):
        text = re.sub(re.escape(part), "[name]", text, flags=re.IGNORECASE)
    return text


def resume_for_model(latex_source: str, name: str = "") -> str:
    """Return the resume as plain text with contact details and the name removed, cut to length."""
    text = redact_contacts(latex_to_text(latex_source))
    return (remove_name(text, name) if name.strip() else text)[:MAX_RESUME_CHARS]


def build_request(job: Job, doc: ProfileDoc, resume_text: str) -> LLMRequest:
    """Build the (PERSONAL tier, non-essential) request for one job."""
    prompt = render(
        load_prompt(PROMPT_NAME, PROMPT_VERSION),
        profile=redact_contacts(profile_text(doc)),
        resume=resume_text,
        company=job.company.name,
        title=job.title,
        posting=(job.raw_text or "(no description)")[:MAX_POSTING_CHARS],
    )
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PERSONAL, max_tokens=900, essential=False)


def tailor_job(
    session: Session,
    router: Router,
    job: Job,
    doc: ProfileDoc,
    latex_source: str,
    routes: Sequence[str],
) -> TailoringNotes | None:
    """Write and save tailoring notes. Returns None if the job isn't scored or is up to date."""
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    if assessment is None or assessment.fit_score is None:
        return None  # only scored (shortlisted) jobs get notes
    resume = resume_for_model(latex_source, doc.name)
    material = (
        f"{resume}\n{profile_text(doc)}\n{job.company.name}\n{job.title}\n{job.raw_text or ''}"
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    saved = assessment.tailoring or {}
    if saved.get("prompt_version") == PROMPT_VERSION and saved.get("resume_sha256") == digest:
        return None
    notes, response = complete_validated_detailed(
        router, build_request(job, doc, resume), TailoringNotes, routes
    )
    assessment.tailoring = {
        "notes": notes.model_dump(),
        "prompt_version": PROMPT_VERSION,
        "resume_sha256": digest,
        "model": f"{response.provider}:{response.model}",
    }
    session.commit()
    return notes
