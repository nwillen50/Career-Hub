"""Warm paths: who the user knows at a company, strongest first, with the reasons.

Plain rules, no model, so the user can see exactly why someone ranks where they do. Points:

| Fact | Points | Reason shown |
| --- | --- | --- |
| The user tagged them with their own words (`how_known`) | 3 | you noted: <their words> |
| Member of an alumni group the user imported | 2 | alumni: <group> |
| In the user's LinkedIn connections | 1 | LinkedIn connection |
| Last contact within a year | 1 | last contact N days ago |
| Title says recruiter or talent | 1 | recruiter |

Ties break by name. A person with no points (for example one with no facts at all) is still listed
last: knowing *someone* there beats nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.companies import find_companies
from careerhub.core.models import Company, Person, utcnow

RECENT = timedelta(days=365)
_ALUMNI = "alumni:"


@dataclass(frozen=True)
class WarmPath:
    """One person the user could ask, with how strong the tie looks and why."""

    person_id: int
    name: str
    title: str | None
    strength: int
    reasons: list[str] = field(default_factory=list)


def _score(person: Person, now: datetime) -> tuple[int, list[str]]:
    points = 0
    reasons: list[str] = []
    notes = [part.strip() for part in (person.how_known or "").split(";") if part.strip()]
    own_words = [note for note in notes if not note.lower().startswith(_ALUMNI)]
    groups = [note[len(_ALUMNI) :].strip() for note in notes if note.lower().startswith(_ALUMNI)]
    if own_words:
        points += 3
        reasons.append(f"you noted: {'; '.join(own_words)}")
    if groups:
        points += 2
        reasons.append(f"alumni: {', '.join(groups)}")
    if person.origin == "linkedin_export":
        points += 1
        reasons.append("LinkedIn connection")
    if person.last_contact_at is not None and now - person.last_contact_at <= RECENT:
        points += 1
        reasons.append(f"last contact {(now - person.last_contact_at).days} days ago")
    title = (person.title or "").lower()
    if "recruit" in title or "talent" in title:
        points += 1
        reasons.append("recruiter")
    return points, reasons


def warm_paths(
    session: Session, company_id: int, limit: int = 3, now: datetime | None = None
) -> list[WarmPath]:
    """Return up to `limit` people at the company, strongest first (empty if none known)."""
    now = now or utcnow()
    people = session.scalars(select(Person).where(Person.company_id == company_id))
    scored = [(person, *_score(person, now)) for person in people]
    scored.sort(key=lambda item: (-item[1], item[0].full_name.lower(), item[0].id))
    return [
        WarmPath(person.id, person.full_name, person.title, points, reasons)
        for person, points, reasons in scored[:limit]
    ]


def find_company(session: Session, text: str) -> Company | None:
    """Return the one company matching `text`, or None if there is none or several match.

    An exact company key (so "Microsoft Corp." finds "Microsoft") wins; otherwise whole-word
    matches count. Use `find_companies` to see the candidates when the match is ambiguous.
    """
    matches = find_companies(session, text)
    return matches[0] if len(matches) == 1 else None
