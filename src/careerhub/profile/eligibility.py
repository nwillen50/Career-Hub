"""What the user is eligible for: their highest degree, graduation date, and work authorization.

Used by the eligibility filters (CH-113). Degree level is read from the free text of the
`education` entries ("BS CS", "M.S. Physics", "Bachelor of Arts"). If the level can't be told, it is
`None` and no job is filtered on it. In-progress degrees count as held, because the user can apply
to jobs that require the degree they are about to finish.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from careerhub.profile.extract import EducationFact
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
    #: Country codes where the user can already work; None when `work_rights` is not set (CH-122).
    work_rights: tuple[str, ...] | None = None


def degree_level(text: str) -> str | None:
    """Return "associate", "bachelor", "master", or "phd" for a degree description, else None."""
    words = set(re.sub(r"[^a-z\s]", "", text.lower().replace(".", "")).split())
    for level in ("phd", "master", "bachelor", "associate"):  # highest first
        if words & _TOKENS[level]:
            return level
    return None


_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
            ("spring",),
            ("fall", "autumn"),
        ),
        start=1,
    )
    for name in names
}
_SEASON_MONTH = {13: 5, 14: 12}  # Spring is May, Fall is December
_WHEN = re.compile(r"(?:\b([A-Za-z]{3,9})\.?\s+)?\b((?:19|20)\d{2})\b(?:-(\d{2})\b)?")


def parse_graduation(text: str | None) -> date | None:
    """Read a graduation date such as "May 2027", "2027-05", "Spring 2027" or a date range.

    Returns the first of the month (Spring is May, Fall is December). In a range like
    "Aug. 2023 -- May 2027 (Expected)" the end counts. Text without a year gives None, and so
    does a year with no month (we won't guess one).
    """
    result: date | None = None
    found = _WHEN.findall(text or "")
    if len(found) == 1 and re.search(r"(?i)\b(present|current|now)\b", text or ""):
        return None  # "Aug 2023 - Present" gives a start, not a graduation
    for word, year, month_digits in found:
        month = None
        if month_digits and 1 <= int(month_digits) <= 12:
            month = int(month_digits)
        elif word:
            number = _MONTHS.get(word.lower())
            month = _SEASON_MONTH.get(number or 0, number)
        if month is not None:
            result = date(int(year), month, 1)
    return result


def education_level(entry: EducationFact) -> str | None:
    """Return the degree level of an accepted education fact (its words first, then its level)."""
    level = degree_level(entry.degree or "")
    if level is None and entry.level and entry.level.strip().lower() in DEGREE_ORDER[1:]:
        level = entry.level.strip().lower()
    return level


def rank(level: str | None) -> int:
    """Order degree levels (unknown ranks as -1)."""
    return DEGREE_ORDER.index(level) if level in DEGREE_ORDER else -1


def eligibility_from(
    doc: ProfileDoc,
    preferences: Preferences | None,
    extra_education: Sequence[EducationFact] | None = None,
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
    # Accepted resume facts: profile.md wins, so they only raise the level or fill a missing date.
    for fact in extra_education or []:
        level, when = education_level(fact), parse_graduation(fact.graduation)
        if rank(level) > rank(best):
            best, graduation = level, when
        elif level == best and graduation is None:
            graduation = when
    rights = tuple(preferences.work_rights) if preferences and preferences.work_rights else None
    return Eligibility(
        best, graduation, preferences.work_authorization if preferences else None, rights
    )
