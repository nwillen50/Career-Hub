"""Imports the connections from a LinkedIn data export into `Person` and `Company` rows.

LinkedIn's export has no API behind it: the user downloads it and unzips it into
`inputs/linkedin/` in the data folder. `Connections.csv` starts with a few lines of notes, then a
header row (`First Name,Last Name,URL,Email Address,Company,Position,Connected On`).

Re-importing is safe: a person is matched by profile URL (or, without one, by name and company),
and only fields that come from LinkedIn (title, company, email) are refreshed. Notes and "how I
know them" written by the user are never touched, and nobody is ever deleted.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Person
from careerhub.core.names import company_key
from careerhub.core.paths import data_dir
from careerhub.profile.files import ProfileFileError

CONNECTIONS_FILENAME = "Connections.csv"
ORIGIN = "linkedin_export"
_HEADER_START = "first name"


@dataclass(frozen=True)
class ImportReport:
    """What an import did. `found` is False when there was no file to read."""

    found: bool
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0  # rows with no name


def connections_path() -> Path:
    """Return where `Connections.csv` should be (it may not exist)."""
    return data_dir() / "inputs" / "linkedin" / CONNECTIONS_FILENAME


def import_connections(session: Session, path: Path | None = None) -> ImportReport:
    """Read `Connections.csv` and add or refresh people. A missing file is reported, not raised.

    Raises:
        ProfileFileError: the file exists but has no recognizable header row.

    """
    path = path or connections_path()
    if not path.exists():
        return ImportReport(found=False)
    created = updated = unchanged = skipped = 0
    for row in _read_rows(path):
        name = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip()
        if not name:
            skipped += 1
            continue
        company = _get_or_create_company(session, row.get("Company", ""))
        person = _find_person(session, name, row.get("URL", ""), company)
        wanted = {
            "title": row.get("Position", "").strip() or None,
            "email": row.get("Email Address", "").strip() or None,
            "company": company,
        }
        if person is None:
            session.add(
                Person(
                    full_name=name,
                    profile_url=row.get("URL", "").strip() or None,
                    origin=ORIGIN,
                    **wanted,
                )
            )
            created += 1
        elif any(getattr(person, field) != value for field, value in wanted.items()):
            for field, value in wanted.items():
                setattr(person, field, value)
            updated += 1
        else:
            unchanged += 1
    session.commit()
    return ImportReport(True, created, updated, unchanged, skipped)


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Return the data rows, skipping the notes that come before the header."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.lower().startswith(_HEADER_START)), None
    )
    if start is None:
        raise ProfileFileError(f"{path.name} has no header row starting with 'First Name'.")
    return [
        {key: (value or "") for key, value in row.items() if key}
        for row in csv.DictReader(lines[start:])
    ]


def _get_or_create_company(session: Session, name: str) -> Company | None:
    """Return the company called `name` (creating it), or None for a blank name."""
    key = company_key(name)
    if not key:
        return None
    company = session.scalars(select(Company).where(Company.name_key == key)).first()
    if company is None:
        company = Company(name=re.sub(r"\s+", " ", name).strip(), name_key=key)
        session.add(company)
        session.flush()
    return company


def _find_person(session: Session, name: str, url: str, company: Company | None) -> Person | None:
    """Find an existing person by profile URL, else by name and company (LinkedIn-sourced only)."""
    url = url.strip()
    if url:
        found = session.scalars(select(Person).where(Person.profile_url == url)).first()
        if found is not None:
            return found
    query = select(Person).where(Person.full_name == name, Person.origin == ORIGIN)
    if company is None:
        query = query.where(Person.company_id.is_(None))
    else:
        query = query.where(Person.company_id == company.id)
    if url:
        # A different URL means a different person who happens to share a name.
        query = query.where(Person.profile_url.is_(None))
    return session.scalars(query).first()
