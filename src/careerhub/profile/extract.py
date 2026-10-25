"""Understand the resume and transcript: structured facts for the user to review (CH-91).

A Personal-tier model task, `profile_extract`, reads the document's text after the name, contact
details, and (for transcripts) identity numbers and addresses have been removed, and returns
`ProfileFacts`. The answer is validated (retry once, then escalate). Results are saved as
**pending** proposals with the file's hash and are redone only when the file or prompt changes;
nothing counts as fact until the user accepts it (CH-110). The call is not `essential`, so the
monthly budget pauses it first. Facts typed into `profile.md` always win over extracted ones.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import ProfileExtraction
from careerhub.llm import LLMRequest, PrivacyTier, Router
from careerhub.llm.validated import complete_validated_detailed
from careerhub.profile.documents import read_document
from careerhub.profile.models import ProfileDoc
from careerhub.profile.redact import personal_text, transcript_text
from careerhub.prompts import load_prompt, render

PROMPT_NAME = "profile_extract"
PROMPT_VERSION = 2
MAX_DOCUMENT_CHARS = 12_000
_Text = Field(default=None, max_length=300)


class _Fact(BaseModel):
    """Base for the fact types: unknown keys are ignored, text is length-limited."""

    model_config = ConfigDict(extra="ignore")


class Experience(_Fact):
    """A job, internship, or activity."""

    role: str = Field(max_length=200)
    organization: str | None = _Text
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    points: list[str] = Field(default_factory=list, max_length=6)


class EducationFact(_Fact):
    """A school and degree."""

    school: str = Field(max_length=200)
    degree: str | None = _Text
    #: "associate", "bachelor", "master", or "phd" when the model could tell (else None).
    level: str | None = Field(default=None, max_length=20)
    graduation: str | None = Field(default=None, max_length=40)


class Course(_Fact):
    """A course with its grade and term."""

    code: str | None = Field(default=None, max_length=40)
    title: str = Field(max_length=200)
    grade: str | None = Field(default=None, max_length=10)
    term: str | None = Field(default=None, max_length=40)


class Project(_Fact):
    """A project."""

    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=600)


class ProfileFacts(_Fact):
    """Everything the model may say about a document. Missing facts stay empty."""

    experiences: list[Experience] = Field(default_factory=list, max_length=15)
    education: list[EducationFact] = Field(default_factory=list, max_length=5)
    courses: list[Course] = Field(default_factory=list, max_length=80)
    gpa: float | None = Field(default=None, ge=0, le=5)
    skills: list[str] = Field(default_factory=list, max_length=40)
    projects: list[Project] = Field(default_factory=list, max_length=15)
    awards: list[str] = Field(default_factory=list, max_length=15)


Kind = Literal["resume", "transcript"]


def file_sha256(path: Path) -> str:
    """Hash the file's bytes, so a changed file is extracted again."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_request(kind: Kind, text: str, doc: ProfileDoc) -> LLMRequest:
    """Build the (PERSONAL tier, non-essential) request; the text is redacted here."""
    redact = transcript_text if kind == "transcript" else personal_text
    prompt = render(
        load_prompt(PROMPT_NAME, PROMPT_VERSION),
        kind=kind,
        document=redact(text, doc.name, doc.aliases)[:MAX_DOCUMENT_CHARS],
    )
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PERSONAL, max_tokens=8000, essential=False)


def extract_and_save(
    session: Session,
    router: Router,
    kind: Kind,
    path: Path,
    doc: ProfileDoc,
    routes: Sequence[str],
) -> ProfileExtraction | None:
    """Extract facts from `path` and save them as a pending proposal.

    Returns None when this file and prompt version were already extracted, or when the document
    couldn't be read (the reader's problems are shown by `hub profile show`). Raises
    `ValidationFailed` if no route answers validly.
    """
    digest = file_sha256(path)
    existing = session.scalars(
        select(ProfileExtraction).where(
            ProfileExtraction.kind == kind,
            ProfileExtraction.file_sha256 == digest,
            ProfileExtraction.prompt_version == PROMPT_VERSION,
        )
    ).first()
    if existing is not None:
        return None
    document = read_document(path)
    if not document.text.strip():
        return None
    facts, response = complete_validated_detailed(
        router, build_request(kind, document.text, doc), ProfileFacts, routes
    )
    row = ProfileExtraction(
        kind=kind,
        file_sha256=digest,
        prompt_version=PROMPT_VERSION,
        facts=facts.model_dump(mode="json"),
        status="pending",
        model_used=f"{response.provider}/{response.model}",
    )
    session.add(row)
    session.commit()
    return row
