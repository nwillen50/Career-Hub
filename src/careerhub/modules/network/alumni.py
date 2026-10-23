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

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.companies import get_or_create_company
from careerhub.core.models import Company, Person
from careerhub.modules.network.layouts import EXTRA_FIELDS, Table, read_table
from careerhub.modules.network.schools import school_key
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
    # ";" separates notes in `how_known`, so one inside a group name would split the tag apart
    return f"alumni: {group.strip().replace(';', ',')}"


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
    aliases: Mapping[str, str] | None = None,
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
        company = _company(session, company_name, aliases)
        email, url = _valid_email(table.get(row, "email")), _valid_url(table.get(row, "url"))
        person = _find(session, name, company, email, url)
        tag = tag_for(row_group)
        school = table.get(row, "school")
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
                    schools=[school] if school else None,
                )
            )
            created += 1
        elif tag in _notes(person.how_known):
            unchanged += 1
            _add_school(person, school)
        else:
            person.how_known = f"{person.how_known}; {tag}" if person.how_known else tag
            _add_school(person, school)
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
    aliases: Mapping[str, str] | None = None,
) -> Preview:
    """Say how `path` would be read and what an import would do, saving nothing."""
    table = read_table(path, sheet, overrides)
    _check(table, path, group)
    try:
        report = import_alumni(
            session, path, group, sheet, overrides, commit=False, aliases=aliases
        )
    finally:
        session.rollback()
    mapping = {name: table.header[index] for name, index in table.mapping.items()}
    return Preview(table.header_row, mapping, tuple(table.ignored), report)


def _add_school(person: Person, school: str) -> None:
    """Remember `school` for `person` (a school already there, by key, is not added again)."""
    if school and school_key(school) not in {school_key(s) for s in person.schools or []}:
        person.schools = [*(person.schools or []), school]


def _company(session: Session, name: str, aliases: Mapping[str, str] | None = None) -> Company:
    company = get_or_create_company(session, name, aliases)
    if company is None:
        company = get_or_create_company(session, "unknown company")
    assert company is not None
    return company


_EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
_URL_SHAPE = re.compile(r"(?i)(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:[/?#]\S*)?")


def _valid_email(value: str) -> str:
    """Return `value` if it looks like an email address, else '' (so 'N/A' or '-' is ignored)."""
    value = value.strip()
    return value if _EMAIL_SHAPE.fullmatch(value) else ""


def _valid_url(value: str) -> str:
    """Return `value` if it looks like a web address, else ''."""
    value = value.strip()
    return value if _URL_SHAPE.fullmatch(value) else ""


def _name_key(name: str) -> str:
    """Fold case (all alphabets, not just ASCII) and collapse spaces, so names compare fairly."""
    return " ".join(name.split()).casefold()


def _find(session: Session, name: str, company: Company, email: str, url: str) -> Person | None:
    """Find an existing person: a matching link or email, or the same name at the same company.

    A shared email or link also needs one word of the name in common ("Ada Sample" and
    "Augusta Ada Sample"), because an office address or a copied cell must not merge different
    people. Names are compared in Python because SQLite folds only ASCII case.
    """
    wanted = _name_key(name)
    for column, value in ((Person.profile_url, url), (Person.email, email.lower())):
        if value:
            for found in session.scalars(select(Person).where(func.lower(column) == value.lower())):
                if set(_name_key(found.full_name).split()) & set(wanted.split()):
                    return found
    for found in session.scalars(select(Person).where(Person.company_id == company.id)):
        if _name_key(found.full_name) == wanted:
            return found
    return None


def _notes(how_known: str | None) -> list[str]:
    """Split `how_known` into its `;`-separated notes (so tags match whole, not by substring)."""
    return [note.strip() for note in (how_known or "").split(";") if note.strip()]
