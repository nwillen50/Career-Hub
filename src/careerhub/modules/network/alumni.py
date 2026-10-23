"""Imports an alumni list the user provides (a club or career-office export) into `Person` rows.

This is the only way alumni enter Career Hub: no school directory is scraped (PRD). The file can be
CSV (any delimiter), TSV, or Excel, with title rows above the header and whatever column names the
source used (`layouts.py` matches them by synonym). Only a name (or first and last name) and a
company are required. The group (school or club) comes from a Group column or from `group=` for the
whole file, which is the usual case.

A person already known (same profile URL, or email, or the same name at the same company) is not
duplicated: the alumni tag is *added* to what the user already has in `how_known`, which is never
overwritten. Class year, major, and location, if present, go in a new person's notes. Re-importing
the same file changes nothing. `preview_alumni` reports how a file would be read and what would
happen, without saving anything and without showing anyone's details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.companies import get_or_create_company
from careerhub.core.models import Company, Person
from careerhub.modules.network.layouts import EXTRA_FIELDS, Table, read_table
from careerhub.profile.files import ProfileFileError

ORIGIN = "alumni_list"


@dataclass(frozen=True)
class AlumniReport:
    """What an alumni import did."""

    created: int = 0
    tagged: int = 0  # already known; the alumni tag was added
    unchanged: int = 0
    skipped: int = 0  # rows missing a name or company
    skipped_for: tuple[str, ...] = ()  # which of "name" and "company" the skipped rows lacked


@dataclass(frozen=True)
class Preview:
    """How a file would be read: column names and counts only, never people's details."""

    header_row: int
    mapping: dict[str, str] = field(default_factory=dict)  # field -> the header it came from
    ignored: tuple[str, ...] = ()
    report: AlumniReport = field(default_factory=AlumniReport)


def tag_for(group: str) -> str:
    """Return the `how_known` text for membership in `group`."""
    return f"alumni: {group.strip()}"


def _check(table: Table, path: Path, group: str | None) -> None:
    """Raise `ProfileFileError` if the file lacks a name, a company, or a group."""
    problems = []
    if not table.has_name:
        problems.append("Name (or First Name and Last Name)")
    if "company" not in table.mapping:
        problems.append("Company")
    if "group" not in table.mapping and not (group and group.strip()):
        problems.append("a Group column (or pass --group)")
    if problems:
        raise ProfileFileError(f"{path.name} needs {', '.join(problems)}.")


def import_alumni(
    session: Session,
    path: Path,
    group: str | None = None,
    sheet: str | None = None,
    overrides: dict[str, str] | None = None,
    commit: bool = True,
) -> AlumniReport:
    """Read the list at `path` and add or tag people. Raises `ProfileFileError` if unusable."""
    table = read_table(path, sheet, overrides)
    _check(table, path, group)
    created = tagged = unchanged = skipped = 0
    lacked: set[str] = set()
    for row in table.rows:
        name = table.person_name(row)
        company_name = table.get(row, "company")
        row_group = (table.get(row, "group") or (group or "")).strip()
        if not (name and company_name and row_group):
            skipped += 1
            lacked.update(n for n, v in (("name", name), ("company", company_name)) if not v)
            continue
        company = _company(session, company_name)
        email, url = table.get(row, "email"), table.get(row, "url")
        person = _find(session, name, company, email, url)
        tag = tag_for(row_group)
        if person is None:
            extras = "; ".join(
                f"{label}: {value}"
                for label, value in ((f.replace("_", " "), table.get(row, f)) for f in EXTRA_FIELDS)
                if value
            )
            session.add(
                Person(
                    full_name=name,
                    company=company,
                    title=table.get(row, "title") or None,
                    email=email or None,
                    profile_url=url or None,
                    origin=ORIGIN,
                    how_known=tag,
                    notes=extras or None,
                )
            )
            created += 1
        elif tag in _notes(person.how_known):
            unchanged += 1
        else:
            person.how_known = f"{person.how_known}; {tag}" if person.how_known else tag
            tagged += 1
        session.flush()  # so a repeat of the same person later in the file is found
    if commit:
        session.commit()
    return AlumniReport(created, tagged, unchanged, skipped, tuple(sorted(lacked)))


def preview_alumni(
    session: Session,
    path: Path,
    group: str | None = None,
    sheet: str | None = None,
    overrides: dict[str, str] | None = None,
) -> Preview:
    """Say how `path` would be read and what an import would do, saving nothing."""
    table = read_table(path, sheet, overrides)
    _check(table, path, group)
    try:
        report = import_alumni(session, path, group, sheet, overrides, commit=False)
    finally:
        session.rollback()
    mapping = {name: table.header[index] for name, index in table.mapping.items()}
    return Preview(table.header_row, mapping, tuple(table.ignored), report)


def _company(session: Session, name: str) -> Company:
    company = get_or_create_company(session, name)
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
