"""Puts every profile input together into one `Profile`, and says what's missing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.models import Person
from careerhub.profile.documents import DocumentText, read_document
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import accepted_facts, counts
from careerhub.profile.files import load_preferences, load_profile_doc
from careerhub.profile.inputs import ResumeFiles, find_resume, find_transcript
from careerhub.profile.models import HardConstraints, Preferences, ProfileDoc
from careerhub.profile.rubric import Rubric, load_rubric


@dataclass(frozen=True)
class DocumentInfo:
    """What `hub profile show` may say about a document: never its text."""

    format: str
    pages: int | None
    chars: int
    problems: tuple[str, ...]

    @classmethod
    def of(cls, document: DocumentText) -> DocumentInfo:
        """Keep only the safe facts about `document`."""
        return cls(document.format, document.pages, document.chars, document.problems)

    def describe(self) -> str:
        """Return e.g. "pdf, 2 pages, 3120 characters"."""
        pages = f", {self.pages} pages" if self.pages is not None else ""
        return f"{self.format}{pages}, {self.chars} characters"


@dataclass(frozen=True)
class Profile:
    """Everything known about the user. Parts that are missing are None, with a reason."""

    doc: ProfileDoc | None
    preferences: Preferences | None
    rubric: Rubric | None
    resume: ResumeFiles | None
    transcript: Path | None
    #: People imported from LinkedIn; None when the database wasn't consulted.
    connections: int | None
    #: One line per missing or unreadable input, ready to show the user.
    problems: list[str] = field(default_factory=list)
    resume_info: DocumentInfo | None = None
    transcript_info: DocumentInfo | None = None
    #: How many extracted fact sets are pending, accepted, or rejected (None: no database).
    fact_counts: dict[str, int] | None = None
    #: Facts the user accepted from the resume and transcript (None: none, or no database).
    accepted: ProfileFacts | None = None


def load_profile(session: Session | None = None) -> Profile:
    """Load all inputs from the data folder; missing ones become `problems`, not errors.

    A file that exists but is malformed still raises `ProfileFileError`: silently skipping a
    broken preferences file could let jobs through that break a hard constraint.
    """
    problems: list[str] = []
    doc = load_profile_doc()
    preferences = load_preferences()
    rubric = load_rubric()
    for value, message in (
        (doc, "profile/profile.md is missing."),
        (preferences, "profile/preferences.yaml is missing."),
        (rubric, "profile/rubric.yaml is missing (a starter rubric is available)."),
    ):
        if value is None:
            problems.append(message)
    resume, why = find_resume()
    if why:
        problems.append(why)
    transcript, why = find_transcript()
    if why:
        problems.append(why)
    resume_info = DocumentInfo.of(resume.read()) if resume else None
    transcript_info = DocumentInfo.of(read_document(transcript)) if transcript else None
    for label, info in (("Resume", resume_info), ("Transcript", transcript_info)):
        if info:
            problems += [f"{label}: {problem}" for problem in info.problems]
    connections = None
    fact_counts = counts(session) if session is not None else None
    if session is not None:
        connections = session.scalar(select(func.count()).select_from(Person)) or 0
    return Profile(
        doc,
        preferences,
        rubric,
        resume,
        transcript,
        connections,
        problems,
        resume_info,
        transcript_info,
        fact_counts,
        accepted_facts(session) if session is not None else None,
    )


QUALIFICATIONS_LINE = (
    "  - {name: qualifications, weight: 2, description: I meet the degree, graduation, and "
    "eligibility requirements.}"
)


def rubric_suggestion(rubric: Rubric | None) -> str | None:
    """Suggest a 'qualifications' criterion when the rubric has none (never edits the file)."""
    if rubric is None or any("qualif" in c.name.lower() for c in rubric.criteria):
        return None
    return (
        "Suggestion: your rubric has no criterion for degree and eligibility fit. To add one, "
        "put this under `criteria:` in rubric.yaml (you decide; nothing was changed):\n"
        + QUALIFICATIONS_LINE
    )


def _modes(hard: HardConstraints) -> str:
    """List the work modes the user allows, e.g. "remote, hybrid (in your locations)"."""
    allowed = []
    if hard.remote_ok:
        allowed.append("remote")
    if hard.hybrid_ok:
        allowed.append("hybrid (in your locations)")
    if hard.onsite_ok:
        allowed.append("onsite (in your locations)")
    return ", ".join(allowed) or "none"


def _plain(text: str) -> str:
    """Make resume-derived text safe to print: control characters (newlines, ANSI) become spaces."""
    return "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in text)


def format_profile(profile: Profile) -> str:
    """Render `profile` as plain text for `hub profile show` (no resume or transcript text)."""
    lines: list[str] = []
    if profile.doc:
        lines += [f"Name: {profile.doc.name}"]
        for school in profile.doc.education:
            when = f", {school.graduation:%b %Y}" if school.graduation else ""
            lines.append(f"Education: {school.degree}, {school.school}{when}")
        typed = {s.strip().lower() for s in profile.doc.skills}
        from_resume = [
            _plain(s)
            for s in (profile.accepted.skills if profile.accepted else [])
            if s.lower() not in typed
        ]
        skills = ", ".join(profile.doc.skills) or "-"
        if from_resume:
            extra = ", ".join(dict.fromkeys(from_resume))
            skills = (
                extra + " (from your resume)"
                if skills == "-"
                else f"{skills}; {extra} (from your resume)"
            )
        lines.append(f"Skills: {skills}")
        lines.append(f"Interests: {', '.join(profile.doc.interests) or '-'}")
        typed_schools = {e.school.strip().lower() for e in profile.doc.education}
        for fact in profile.accepted.education if profile.accepted else []:
            if fact.school.strip().lower() not in typed_schools:
                detail = _plain(
                    ", ".join(x for x in (fact.degree, fact.school, fact.graduation) if x)
                )
                lines.append(f"Education: {detail} (from your resume)")
    if profile.preferences:
        hard = profile.preferences.hard_constraints
        lines += [
            f"Locations: {', '.join(hard.locations) or 'anywhere'}",
            f"Work modes allowed: {_modes(hard)}",
            f"Minimum salary: {hard.min_salary_usd or '-'}",
            f"Excluded companies: {', '.join(hard.excluded_companies) or '-'}",
            f"Excluded title words: {', '.join(hard.excluded_title_words) or '-'}",
            f"Weekly time budget: {profile.preferences.weekly_time_budget_hours} hours",
        ]
    if profile.rubric:
        lines.append(f"Rubric v{profile.rubric.version}:")
        lines += [f"  {c.weight:g} x {c.name}" for c in profile.rubric.criteria]
    for label, path, info in (
        ("Resume", profile.resume.main if profile.resume else None, profile.resume_info),
        ("Transcript", profile.transcript, profile.transcript_info),
    ):
        detail = f" ({info.describe()})" if info else ""
        lines.append(f"{label}: {path.name if path else 'not found'}{detail}")
    connections = (
        "unknown (database not opened)" if profile.connections is None else profile.connections
    )
    lines.append(f"LinkedIn connections imported: {connections}")
    if profile.fact_counts is not None:
        lines.append(
            f"Facts read from documents: {profile.fact_counts['pending']} waiting for review, "
            f"{profile.fact_counts['accepted']} accepted"
        )
    suggestion = rubric_suggestion(profile.rubric)
    if suggestion:
        lines += ["", suggestion]
    if profile.problems:
        lines += ["", "Missing:"] + [f"  - {problem}" for problem in profile.problems]
    return "\n".join(lines)
