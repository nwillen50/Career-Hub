"""Finding and tagging people. Tagging is the user's own statement of how they know someone."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Person
from careerhub.modules.network.schools import school_key


class UnknownPerson(KeyError):
    """No person has that id."""


def find_people(session: Session, text: str, limit: int = 20) -> list[Person]:
    """Return people whose name or company contains `text` (case-insensitive), by name."""
    like = f"%{text.strip().lower()}%"
    query = (
        select(Person)
        .outerjoin(Company, Person.company_id == Company.id)
        .where(or_(func.lower(Person.full_name).like(like), func.lower(Company.name).like(like)))
        .order_by(Person.full_name, Person.id)
        .limit(limit)
    )
    return list(session.scalars(query))


def tag_person(
    session: Session, person_id: int, how_known: str, school: str | None = None
) -> Person:
    """Set how the user knows `person_id` (replaces the old text, e.g. "former manager").

    Alumni tags ("alumni: Tufts") that an import added are kept: they feed warm paths, and only the
    user's own words are replaced (CH-155).

    With `school`, also record a school this person attended (added to the ones already known).
    """
    person = session.get(Person, person_id)
    if person is None:
        raise UnknownPerson(person_id)
    if how_known.strip() or school is None:
        kept = [
            n.strip()
            for n in (person.how_known or "").split(";")
            if n.strip().startswith("alumni:")
        ]
        person.how_known = (
            "; ".join([*([how_known.strip()] if how_known.strip() else []), *kept]) or None
        )
    if (
        school
        and school.strip()
        and school_key(school) not in {school_key(s) for s in person.schools or []}
    ):
        person.schools = [*(person.schools or []), " ".join(school.split())]
    session.commit()
    return person
