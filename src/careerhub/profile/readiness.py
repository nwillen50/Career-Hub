"""Decide whether the profile is ready for scoring (CH-102).

While the profile is still being built, scores would change every night and cost money for nothing.
Scoring and tailoring stay off until the user runs `hub profile ready`, which first checks this
list. Accepted resume facts count toward skills and education. The switch is a small file in the
data folder (`profile/ready.json`) holding the date; `hub profile ready --off` removes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from careerhub.core.files import write_private
from careerhub.core.paths import data_dir
from careerhub.profile.documents import read_document
from careerhub.profile.facts import accepted_facts
from careerhub.profile.files import ProfileFileError, load_preferences, load_profile_doc
from careerhub.profile.inputs import find_resume
from careerhub.profile.rubric import load_rubric

READY_FILENAME = "ready.json"
DEFAULT_MIN_SKILLS = 5


@dataclass(frozen=True)
class Readiness:
    """Whether scoring is switched on, and what is still missing from the profile."""

    on: bool
    since: date | None = None
    missing: list[str] = field(default_factory=list)


def ready_path() -> Path:
    """Return where the switch is stored (it may not exist)."""
    return data_dir() / "profile" / READY_FILENAME


def scoring_since() -> date | None:
    """Return the date scoring was switched on, or None if it is off."""
    path = ready_path()
    try:
        return date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["since"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set_scoring(on: bool, today: date) -> None:
    """Switch scoring on (stamped with `today`) or off."""
    path = ready_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, json.dumps({"since": today.isoformat()}) + "\n")
    else:
        path.unlink(missing_ok=True)


def missing_items(session: Session | None, min_skills: int = DEFAULT_MIN_SKILLS) -> list[str]:
    """List what the profile still lacks (empty when it is ready). Never reads document text out."""
    missing: list[str] = []
    try:
        doc, preferences = load_profile_doc(), load_preferences()
    except ProfileFileError:
        return ["profile.md or preferences.yaml has a problem (run hub profile show)"]
    facts = accepted_facts(session) if session is not None else None
    if doc is None:
        return ["profile.md is missing"]
    skills = {s.strip().lower() for s in doc.skills if s.strip()}
    if facts:
        skills |= {s.strip().lower() for s in facts.skills if s.strip()}
    if not any(e.graduation for e in doc.education):
        missing.append("education with an expected graduation date (in profile.md)")
    if len(skills) < min_skills:
        missing.append(f"at least {min_skills} skills (you have {len(skills)})")
    if not (doc.interests or doc.about.strip()):
        missing.append("interests or an About section")
    resume, _ = find_resume()
    if resume is None or not read_document(resume.main).text.strip():
        missing.append("a resume that can be read (inputs/resume)")
    if preferences is None or not preferences.hard_constraints.locations:
        missing.append("locations in preferences.yaml (work modes default to all allowed)")
    if load_rubric() is None:
        missing.append("rubric.yaml")
    return missing


def current_readiness(session: Session | None, min_skills: int = DEFAULT_MIN_SKILLS) -> Readiness:
    """Return the switch and, while it is off, what is missing."""
    since = scoring_since()
    if since is not None:
        return Readiness(True, since, [])
    return Readiness(False, None, missing_items(session, min_skills))
