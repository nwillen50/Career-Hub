"""What the user is eligible for: their highest degree, graduation date, and work authorization.

Used by the eligibility filters (CH-113). Degree level is read from the free text of the
`education` entries ("BS CS", "M.S. Physics", "Bachelor of Arts"). If the level can't be told, it is
`None` and no job is filtered on it. In-progress degrees count as held, because the user can apply
to jobs that require the degree they are about to finish.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from careerhub.profile.models import Preferences, ProfileDoc

DEGREE_ORDER = ("none", "associate", "bachelor", "master", "phd")
_TOKENS = {
    "phd": {"phd", "doctor", "doctorate", "dphil", "edd"},
    "master": {"master", "masters", "ms", "msc", "ma", "mba", "meng", "mfa", "mphil", "msw"},
    "bachelor": {"bachelor", "bachelors", "bs", "bsc", "ba", "beng", "bse", "bfa", "sb", "ab"},
    "associate": {"associate", "associates", "aa", "aas"},
}


@dataclass(frozen=True)
class Eligibility:
    """The facts the eligibility filters compare a job against."""

    highest_degree: str | None = None
    graduation: date | None = None
    #: From preferences.yaml: "us_citizen", "work_authorized", or "needs_sponsorship".
    work_authorization: str | None = None


def degree_level(text: str) -> str | None:
    """Return "associate", "bachelor", "master", or "phd" for a degree description, else None."""
    words = set(re.sub(r"[^a-z\s]", "", text.lower().replace(".", "")).split())
    for level in ("phd", "master", "bachelor", "associate"):  # highest first
        if words & _TOKENS[level]:
            return level
    return None


def rank(level: str | None) -> int:
    """Order degree levels (unknown ranks as -1)."""
    return DEGREE_ORDER.index(level) if level in DEGREE_ORDER else -1


def eligibility_from(
    doc: ProfileDoc, preferences: Preferences | None, extra_degrees: list[str] | None = None
) -> Eligibility:
    """Work out the user's eligibility from `profile.md`, preferences, and accepted resume facts."""
    best: str | None = None
    graduation: date | None = None
    for entry in doc.education:
        level = degree_level(entry.degree)
        if rank(level) > rank(best):
            best, graduation = level, entry.graduation
        elif (
            level == best
            and entry.graduation
            and (graduation is None or entry.graduation > graduation)
        ):
            graduation = entry.graduation
    for text in extra_degrees or []:
        level = degree_level(text)
        if rank(level) > rank(best):
            best, graduation = level, None
    return Eligibility(best, graduation, preferences.work_authorization if preferences else None)
