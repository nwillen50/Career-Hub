"""Imports an alumni list the user provides (a club or career-office export) into `Person` rows.

This is the only way alumni enter Career Hub: no school directory is scraped (PRD). The CSV needs a
header row with these columns (any order, any capitalization):

    Name (or First Name and Last Name), Company, Group     required: Group is the school or club
    Title, Email, URL                                      optional

A person already known (same profile URL, or email, or the same name at the same company) is not
duplicated: the alumni tag is *added* to what the user already has in `how_known`, which is never
overwritten. Re-importing the same file changes nothing.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.companies import get_or_create_company
from careerhub.core.models import Company, Person
from careerhub.profile.files import ProfileFileError

ORIGIN = "alumni_list"
_REQUIRED = {"company", "group"}


@dataclass(frozen=True)
class AlumniReport:
    """What an alumni import did."""

    created: int = 0
    tagged: int = 0  # already known; the alumni tag was added
    unchanged: int = 0
    skipped: int = 0  # rows missing a name, company, or group


def tag_for(group: str) -> str:
    """Return the `how_known` text for membership in `group`."""
    return f"alumni: {group.strip()}"


def import_alumni(
    session: Session, path: Path, aliases: Mapping[str, str] | None = None
) -> AlumniReport:
    """Read the alumni CSV at `path`. Raises `ProfileFileError` if it has the wrong columns."""
    if not path.exists():
        raise ProfileFileError(f"{path.name} does not exist.")
    created = tagged = unchanged = skipped = 0
    for row in _rows(path):
        name = row.get("name") or f"{row.get('first name', '')} {row.get('last name', '')}".strip()
        company_name, group = row.get("company", "").strip(), row.get("group", "").strip()
        if not (name and company_name and group):
            skipped += 1
            continue
        company = _company(session, company_name, aliases)
        person = _find(session, name, company, row.get("email", ""), row.get("url", ""))
        tag = tag_for(group)
        if person is None:
            session.add(
                Person(
                    full_name=name,
                    company=company,
                    title=row.get("title", "").strip() or None,
                    email=row.get("email", "").strip() or None,
                    profile_url=row.get("url", "").strip() or None,
                    origin=ORIGIN,
                    how_known=tag,
                )
            )
            created += 1
        elif tag in _notes(person.how_known):
            unchanged += 1
        else:
            person.how_known = f"{person.how_known}; {tag}" if person.how_known else tag
            tagged += 1
    session.commit()
    return AlumniReport(created, tagged, unchanged, skipped)


def _rows(path: Path) -> list[dict[str, str]]:
    """Read the CSV with lowercase column names, checking the required columns exist."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = {(name or "").strip().lower() for name in reader.fieldnames or []}
        if not _REQUIRED <= names or not (
            {"name"} <= names or {"first name", "last name"} <= names
        ):
            raise ProfileFileError(
                f"{path.name} needs columns Name (or First Name and Last Name), Company, and Group."
            )
        return [
            {(key or "").strip().lower(): (value or "") for key, value in row.items()}
            for row in reader
        ]


def _company(session: Session, name: str, aliases: Mapping[str, str] | None = None) -> Company:
    company = get_or_create_company(session, name, aliases)
    if company is None:
        company = get_or_create_company(session, "unknown company")
    assert company is not None
    return company


def _find(session: Session, name: str, company: Company, email: str, url: str) -> Person | None:
    """Find an existing person by URL, then email, then name at the same company."""
    for column, value in ((Person.profile_url, url.strip()), (Person.email, email.strip().lower())):
        if value:
            found = session.scalars(
                select(Person).where(func.lower(column) == value.lower())
            ).first()
            if found is not None:
                return found
    return session.scalars(
        select(Person).where(
            func.lower(Person.full_name) == " ".join(name.split()).lower(),
            Person.company_id == company.id,
        )
    ).first()


def _notes(how_known: str | None) -> list[str]:
    """Split `how_known` into its `;`-separated notes (so tags match whole, not by substring)."""
    return [note.strip() for note in (how_known or "").split(";") if note.strip()]
