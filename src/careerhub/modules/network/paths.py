"""Warm paths: who the user knows at a company, strongest first, with the reasons.

Plain rules, no model, so the user can see exactly why someone ranks where they do. Points:

| Fact | Points | Reason shown |
| --- | --- | --- |
| The user tagged them with their own words (`how_known`) | 3 | you noted: <their words> |
| Member of an alumni group the user imported | 2 | alumni: <group> |
| Went to one of the user's schools (from an import or a tag) | 2 | Same school: <school> |
| In the user's LinkedIn connections | 1 | LinkedIn connection |
| Last contact within a year | 1 | last contact N days ago |
| Title says recruiter or talent | 1 | recruiter |

A same-school person and an alumni-list member for the same school count once (2 points, not 4).
Ties break by name. A person with no points (for example one with no facts at all) is still listed
last: knowing *someone* there beats nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.companies import find_companies
from careerhub.core.models import Company, Person, utcnow
from careerhub.modules.network.schools import (
    my_school_aliases,
    my_schools,
    related_school,
    same_school,
)
from careerhub.modules.network.schools_table import RANK

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
    #: The school this person shares with the user, if any (the page marks them "Same school").
    same_school: str | None = None
    #: A weaker school tie when it is not the same school: "Branch campus: Penn State" or
    #: "Same system: University of California" (CH-179).
    school_tie: str | None = None
    #: 2 for a branch campus, 1 for the same system, 0 for none; breaks ties between people.
    tie_rank: int = 0


def _score(
    person: Person,
    now: datetime,
    mine: Sequence[str] = (),
    aliases: dict[str, list[str]] | None = None,
) -> tuple[int, list[str], str | None, str | None, int]:
    points = 0
    reasons: list[str] = []
    notes = [part.strip() for part in (person.how_known or "").split(";") if part.strip()]
    own_words = [note for note in notes if not note.lower().startswith(_ALUMNI)]
    groups = [note[len(_ALUMNI) :].strip() for note in notes if note.lower().startswith(_ALUMNI)]
    if own_words:
        points += 3
        reasons.append(f"you noted: {'; '.join(own_words)}")
    shared = same_school(person.schools or [], mine, aliases)
    tie = None if shared else related_school(person.schools or [], mine, aliases)
    if shared:
        points += 2  # counts once even if an alumni list names the same school
        reasons.append(f"Same school: {shared}")
    elif tie:
        points += 1 if tie.kind == "branch" else 0  # a same-system tie only breaks ties
        reasons.append(tie.label or "")
    if groups:
        points += 0 if shared else 2
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
    rank = 3 if shared else RANK[tie.kind] if tie else 0
    return points, reasons, shared, tie.label if tie else None, rank


def warm_paths(
    session: Session,
    company_id: int,
    limit: int = 3,
    now: datetime | None = None,
    schools: Sequence[str] | None = None,
) -> list[WarmPath]:
    """Return up to `limit` people at the company, strongest first (empty if none known).

    `schools` are the user's schools; by default they are read from `profile.md` and accepted facts.
    """
    now = now or utcnow()
    mine = my_schools(session) if schools is None else list(schools)
    aliases = my_school_aliases() if mine else {}
    people = session.scalars(select(Person).where(Person.company_id == company_id))
    scored = [(person, *_score(person, now, mine, aliases)) for person in people]
    scored.sort(key=lambda item: (-item[1], -item[5], item[0].full_name.lower(), item[0].id))
    return [
        WarmPath(person.id, person.full_name, person.title, points, reasons, shared, label, rank)
        for person, points, reasons, shared, label, rank in scored[:limit]
    ]


def find_company(session: Session, text: str) -> Company | None:
    """Return the one company matching `text`, or None if there is none or several match.

    An exact company key (so "Microsoft Corp." finds "Microsoft") wins; otherwise whole-word
    matches count. Use `find_companies` to see the candidates when the match is ambiguous.
    """
    matches = find_companies(session, text)
    return matches[0] if len(matches) == 1 else None
