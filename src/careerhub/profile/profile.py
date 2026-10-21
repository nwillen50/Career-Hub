"""Puts every profile input together into one `Profile`, and says what's missing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.models import Person
from careerhub.profile.files import load_preferences, load_profile_doc
from careerhub.profile.inputs import ResumeFiles, find_resume, find_transcript
from careerhub.profile.models import Preferences, ProfileDoc
from careerhub.profile.rubric import Rubric, load_rubric


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
    connections = None
    if session is not None:
        connections = session.scalar(select(func.count()).select_from(Person)) or 0
    return Profile(doc, preferences, rubric, resume, transcript, connections, problems)


def format_profile(profile: Profile) -> str:
    """Render `profile` as plain text for `hub profile show` (no resume or transcript text)."""
    lines: list[str] = []
    if profile.doc:
        lines += [f"Name: {profile.doc.name}"]
        for school in profile.doc.education:
            when = f", {school.graduation:%b %Y}" if school.graduation else ""
            lines.append(f"Education: {school.degree}, {school.school}{when}")
        lines.append(f"Skills: {', '.join(profile.doc.skills) or '-'}")
        lines.append(f"Interests: {', '.join(profile.doc.interests) or '-'}")
    if profile.preferences:
        hard = profile.preferences.hard_constraints
        lines += [
            f"Locations: {', '.join(hard.locations) or 'anywhere'} (remote ok: {hard.remote_ok})",
            f"Minimum salary: {hard.min_salary_usd or '-'}",
            f"Excluded companies: {', '.join(hard.excluded_companies) or '-'}",
            f"Excluded title words: {', '.join(hard.excluded_title_words) or '-'}",
            f"Weekly time budget: {profile.preferences.weekly_time_budget_hours} hours",
        ]
    if profile.rubric:
        lines.append(f"Rubric v{profile.rubric.version}:")
        lines += [f"  {c.weight:g} x {c.name}" for c in profile.rubric.criteria]
    lines.append(f"Resume: {profile.resume.main.name if profile.resume else 'not found'}")
    lines.append(f"Transcript: {profile.transcript.name if profile.transcript else 'not found'}")
    connections = (
        "unknown (database not opened)" if profile.connections is None else profile.connections
    )
    lines.append(f"LinkedIn connections imported: {connections}")
    if profile.problems:
        lines += ["", "Missing:"] + [f"  - {problem}" for problem in profile.problems]
    return "\n".join(lines)
