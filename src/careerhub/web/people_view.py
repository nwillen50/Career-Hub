"""What the Contacts and Interactions tabs show (CH-171): people, the contact log, follow-ups.

Dates are shown and typed in the user's timezone (YYYY-MM-DD). Nothing here contacts anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Interaction, Person
from careerhub.core.settings import load_settings
from careerhub.modules.network.interactions import CHANNELS

MAX_CONTACTS = 300
MAX_INTERACTIONS = 100
_IMPORTED_PREFIX = "alumni:"


@dataclass(frozen=True)
class ContactView:
    """One person as the Contacts table shows them."""

    id: int
    name: str
    company: str
    title: str
    own_words: str
    imported_tags: str
    last_contact: str


@dataclass(frozen=True)
class InteractionView:
    """One logged contact as the Interactions table shows it."""

    id: int
    person_id: int
    person: str
    company: str
    when: str
    channel: str
    summary: str
    follow_up: str
    done: bool
    overdue: bool


def _day(moment: datetime | None, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).date().isoformat() if moment else ""


def split_how_known(how_known: str | None) -> tuple[str, str]:
    """Split `how_known` into the user's own words and the imported alumni tags."""
    notes = [n.strip() for n in (how_known or "").split(";") if n.strip()]
    imported = [n for n in notes if n.lower().startswith(_IMPORTED_PREFIX)]
    own = [n for n in notes if not n.lower().startswith(_IMPORTED_PREFIX)]
    return "; ".join(own), "; ".join(imported)


def gather_contacts(session: Session, text: str = "") -> list[ContactView]:
    """Return people (by name), optionally narrowed by a name or company fragment."""
    zone = ZoneInfo(load_settings().timezone)
    query = select(Person).outerjoin(Company, Person.company_id == Company.id)
    if text.strip():
        # escape LIKE wildcards so a typed % or _ matches itself, not everyone
        typed = text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{typed}%"
        query = query.where(
            or_(
                func.lower(Person.full_name).like(like, escape="\\"),
                func.lower(Company.name).like(like, escape="\\"),
            )
        )
    people = session.scalars(query.order_by(Person.full_name, Person.id).limit(MAX_CONTACTS)).all()
    latest = {
        person_id: moment
        for person_id, moment in session.execute(
            select(Interaction.person_id, func.max(Interaction.occurred_at)).group_by(
                Interaction.person_id
            )
        )
    }
    views = []
    for person in people:
        own, imported = split_how_known(person.how_known)
        views.append(
            ContactView(
                id=person.id,
                name=person.full_name,
                company=person.company.name if person.company else "",
                title=person.title or "",
                own_words=own,
                imported_tags=imported,
                last_contact=_day(latest.get(person.id), zone),
            )
        )
    return views


def gather_interactions(session: Session, now: datetime) -> list[InteractionView]:
    """Return the newest logged contacts, each with its follow-up state."""
    zone = ZoneInfo(load_settings().timezone)
    labels = dict(CHANNELS)
    rows = session.execute(
        select(Interaction, Person)
        .join(Person, Person.id == Interaction.person_id)
        .order_by(Interaction.occurred_at.desc(), Interaction.id.desc())
        .limit(MAX_INTERACTIONS)
    ).all()
    return [
        InteractionView(
            id=i.id,
            person_id=p.id,
            person=p.full_name,
            company=p.company.name if p.company else "",
            when=_day(i.occurred_at, zone),
            channel=labels.get(i.channel, i.channel),
            summary=i.summary or "",
            follow_up=_day(i.follow_up_due, zone),
            done=i.follow_up_done,
            overdue=bool(i.follow_up_due and not i.follow_up_done and i.follow_up_due <= now),
        )
        for i, p in rows
    ]


def people_choices(session: Session) -> list[tuple[int, str]]:
    """Return (id, "Name, Company") for the "who" menu, by name."""
    rows = session.execute(
        select(Person.id, Person.full_name, Company.name)
        .outerjoin(Company, Person.company_id == Company.id)
        .order_by(Person.full_name, Person.id)
        .limit(MAX_CONTACTS)
    ).all()
    return [(pid, f"{name}, {firm}" if firm else name) for pid, name, firm in rows]
