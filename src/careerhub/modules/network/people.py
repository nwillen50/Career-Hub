"""Finding and tagging people. Tagging is the user's own statement of how they know someone."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Person


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


def tag_person(session: Session, person_id: int, how_known: str) -> Person:
    """Set how the user knows `person_id` (replaces the old text, e.g. "former manager")."""
    person = session.get(Person, person_id)
    if person is None:
        raise UnknownPerson(person_id)
    person.how_known = how_known.strip() or None
    session.commit()
    return person
