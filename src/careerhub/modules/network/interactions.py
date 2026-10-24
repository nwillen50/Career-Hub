"""Logging contacts with people, follow-ups, and editing how the user knows someone (CH-169).

Everything here records what the user did or plans to do. Nothing is sent: a follow-up is a date
on a list, and "done" is the user saying so.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Interaction, Person, utcnow
from careerhub.modules.network.people import UnknownPerson

#: How a contact happened, as (stored word, label).
CHANNELS = (
    ("email", "Email"),
    ("linkedin", "LinkedIn message"),
    ("call", "Call"),
    ("in_person", "In person"),
    ("other", "Other"),
)
MAX_SUMMARY = 1000
MAX_HOW_KNOWN = 500
#: Imported alumni tags look like "alumni: Tufts Jumbos"; they are not the user's own words.
_IMPORTED_PREFIX = "alumni:"


class InteractionError(ValueError):
    """The change can't be made; the message says why in plain words."""


def log_interaction(
    session: Session,
    person_id: int,
    channel: str,
    summary: str | None = None,
    occurred_at: datetime | None = None,
    follow_up_due: datetime | None = None,
) -> Interaction:
    """Record a contact with a person, with an optional follow-up date.

    A follow-up date of today (the start of the day) or earlier is allowed; it simply shows as due.
    """
    if session.get(Person, person_id) is None:
        raise UnknownPerson(person_id)
    if channel not in dict(CHANNELS):
        raise InteractionError(f"Unknown channel {channel!r}.")
    text = (summary or "").strip()
    if len(text) > MAX_SUMMARY:
        raise InteractionError(f"Keep the summary under {MAX_SUMMARY} characters.")
    when = occurred_at or utcnow()
    interaction = Interaction(
        person_id=person_id,
        occurred_at=when,
        channel=channel,
        summary=text or None,
        follow_up_due=follow_up_due,
    )
    session.add(interaction)
    session.commit()
    return interaction


def set_follow_up_done(session: Session, interaction_id: int, done: bool = True) -> Interaction:
    """Mark an interaction's follow-up done (or not done again)."""
    interaction = session.get(Interaction, interaction_id)
    if interaction is None:
        raise InteractionError("That interaction is gone.")
    if interaction.follow_up_due is None:
        raise InteractionError("That interaction has no follow-up date.")
    interaction.follow_up_done = done
    session.commit()
    return interaction


def recent_interactions(session: Session, limit: int = 100) -> list[Interaction]:
    """Return the newest interactions first (follow-ups still open are not special here)."""
    return list(
        session.scalars(
            select(Interaction)
            .order_by(Interaction.occurred_at.desc(), Interaction.id.desc())
            .limit(limit)
        )
    )


def set_own_words(session: Session, person_id: int, text: str, commit: bool = True) -> Person:
    """Replace what the user wrote in `how_known`, keeping imported alumni tags.

    `how_known` holds `;`-separated notes. Notes that start with "alumni:" came from an import
    and survive an edit; everything else is the user's and is replaced by `text`.
    """
    person = session.get(Person, person_id)
    if person is None:
        raise UnknownPerson(person_id)
    own = " ".join(text.replace(";", ",").split())
    kept = [
        note.strip()
        for note in (person.how_known or "").split(";")
        if note.strip().lower().startswith(_IMPORTED_PREFIX)
    ]
    combined = "; ".join([own, *kept] if own else kept)
    if len(combined) > MAX_HOW_KNOWN:
        raise InteractionError("That is too long. Keep it to a short phrase.")
    person.how_known = combined or None
    if commit:
        session.commit()
    return person


#: What a typed email or phone number may look like (loose on purpose: people type many forms).
_EMAIL = re.compile(r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+")
_PHONE = re.compile(r"\+?[0-9][0-9 ().\-]{4,38}[0-9]")


def set_contact_details(
    session: Session,
    person_id: int,
    email: str | None,
    phone: str | None,
    commit: bool = True,
) -> Person:
    """Set a person's email and phone as typed on the Contacts page (blank clears one).

    `None` leaves that one as it is (a request that names only one of them). With `commit=False`
    the caller commits, so a later failed check can still undo this one.

    Both are Sensitive: they are stored in the encrypted database and shown only on the local
    page; nothing here logs them or sends them anywhere.
    """
    person = session.get(Person, person_id)
    if person is None:
        raise UnknownPerson(person_id)
    mail = None if email is None else email.strip()
    number = None if phone is None else " ".join(phone.split())
    if mail and (len(mail) > 320 or not _EMAIL.fullmatch(mail)):
        raise InteractionError("That does not look like an email address.")
    if number and not _PHONE.fullmatch(number):
        raise InteractionError("A phone number may have digits, spaces, + ( ) . and -, up to 40.")
    if mail is not None:
        person.email = mail or None
    if number is not None:
        person.phone = number or None
    if commit:
        session.commit()
    return person
