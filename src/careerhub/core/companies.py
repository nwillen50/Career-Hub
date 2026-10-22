"""Find, create, and merge companies by their key (CH-86). Every importer uses these."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Job, Person
from careerhub.core.names import company_key


def get_or_create_company(session: Session, name: str) -> Company | None:
    """Return the company called `name` (matched by key), creating it; None for a blank name."""
    key = company_key(name)
    if not key:
        return None
    company = session.scalars(select(Company).where(Company.name_key == key)).first()
    if company is None:
        company = Company(name=" ".join(name.split()), name_key=key)
        session.add(company)
        session.flush()
    return company


def find_companies(session: Session, text: str) -> list[Company]:
    """Return the companies matching `text`: the exact key if there is one, else whole-word hits.

    "Meta" does not match "Metabase" (only whole words count), and several matches are all
    returned so the caller can report the ambiguity instead of picking one.
    """
    key = company_key(text)
    if not key:
        return []
    exact = session.scalars(select(Company).where(Company.name_key == key)).first()
    if exact is not None:
        return [exact]
    word = re.compile(rf"(?<!\w){re.escape(key)}(?!\w)")
    everyone = session.scalars(select(Company).order_by(Company.name_key)).all()
    return [c for c in everyone if word.search(c.name_key)]


def merge_companies(session: Session, keep: Company, drop: Company) -> None:
    """Move everything from `drop` to `keep` and delete `drop`."""
    for job in session.scalars(select(Job).where(Job.company_id == drop.id)):
        job.company_id = keep.id
    for person in session.scalars(select(Person).where(Person.company_id == drop.id)):
        person.company_id = keep.id
    keep.notes = "\n".join(t for t in (keep.notes, drop.notes) if t) or None
    keep.size = keep.size or drop.size
    keep.industry = keep.industry or drop.industry
    keep.domains = sorted({*keep.domains, *drop.domains})
    keep.ats_board_ids = {**drop.ats_board_ids, **keep.ats_board_ids}
    session.flush()
    session.delete(drop)
    session.flush()
