from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Person
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.profile import ProfileFileError
from careerhub.profile.linkedin import company_key, connections_path, import_connections

NOTES = """Notes:
"When exporting your connections data, you may notice that some email addresses are missing."

"""
HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
ROW_A = "Ada,Sample,https://example.com/in/ada,,Acme  Corp,Engineer,05 Oct 2024\n"
ROW_B = "Ben,Example,https://example.com/in/ben,ben@example.com,Globex,Analyst,01 Jan 2023\n"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def csv_file(tmp_path: Path, *rows: str, notes: str = NOTES) -> Path:
    path = tmp_path / "Connections.csv"
    path.write_text(notes + HEADER + "".join(rows), encoding="utf-8")
    return path


def test_people_and_companies_are_created(tmp_path: Path, session: Session) -> None:
    report = import_connections(session, csv_file(tmp_path, ROW_A, ROW_B))
    assert (report.found, report.created, report.updated) == (True, 2, 0)
    ada = session.scalars(select(Person).where(Person.full_name == "Ada Sample")).one()
    assert ada.title == "Engineer" and ada.origin == "linkedin_export"
    assert ada.company is not None and ada.company.name == "Acme Corp"
    assert ada.email is None


def test_reimport_adds_no_duplicates(tmp_path: Path, session: Session) -> None:
    path = csv_file(tmp_path, ROW_A, ROW_B)
    import_connections(session, path)
    again = import_connections(session, path)
    assert (again.created, again.updated, again.unchanged) == (0, 0, 2)
    assert len(session.scalars(select(Person)).all()) == 2
    assert len(session.scalars(select(Company)).all()) == 2


def test_a_changed_title_is_updated_but_user_notes_are_kept(
    tmp_path: Path, session: Session
) -> None:
    import_connections(session, csv_file(tmp_path, ROW_A))
    ada = session.scalars(select(Person)).one()
    ada.notes, ada.how_known = "met at a fair", "former teammate"
    session.commit()
    promoted = ROW_A.replace("Engineer", "Senior Engineer")
    report = import_connections(session, csv_file(tmp_path, promoted))
    assert report.updated == 1
    session.refresh(ada)
    assert ada.title == "Senior Engineer"
    assert (ada.notes, ada.how_known) == ("met at a fair", "former teammate")


def test_nobody_is_deleted_when_missing_from_a_new_export(tmp_path: Path, session: Session) -> None:
    import_connections(session, csv_file(tmp_path, ROW_A, ROW_B))
    import_connections(session, csv_file(tmp_path, ROW_A))
    assert len(session.scalars(select(Person)).all()) == 2


def test_companies_match_ignoring_case_and_spacing(tmp_path: Path, session: Session) -> None:
    session.add(Company(name="Acme Corp", name_key="acme corp"))
    session.commit()
    import_connections(session, csv_file(tmp_path, ROW_A.replace("Acme  Corp", "ACME corp")))
    assert len(session.scalars(select(Company)).all()) == 1
    assert company_key("  Acme   Corp ") == "acme corp"


def test_rows_without_a_name_are_skipped_and_blank_company_is_none(
    tmp_path: Path, session: Session
) -> None:
    rows = (",,https://example.com/in/x,,Acme,Boss,01 Jan 2020\n", "Cy,Free,,,,,01 Jan 2020\n")
    report = import_connections(session, csv_file(tmp_path, *rows))
    assert (report.created, report.skipped) == (1, 1)
    assert session.scalars(select(Person)).one().company is None


def test_same_name_with_different_urls_are_different_people(
    tmp_path: Path, session: Session
) -> None:
    other = ROW_A.replace("/in/ada", "/in/ada2")
    report = import_connections(session, csv_file(tmp_path, ROW_A, other))
    assert report.created == 2


def test_missing_file_is_reported_not_raised(tmp_path: Path, session: Session) -> None:
    assert import_connections(session, tmp_path / "nope.csv").found is False


def test_file_without_a_header_is_an_error(tmp_path: Path, session: Session) -> None:
    path = tmp_path / "Connections.csv"
    path.write_text("just,some,text\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ProfileFileError, match="header row"):
        import_connections(session, path)


def test_byte_order_mark_is_ignored(tmp_path: Path, session: Session) -> None:
    path = tmp_path / "Connections.csv"
    path.write_text(HEADER + ROW_A, encoding="utf-8-sig")
    assert import_connections(session, path).created == 1


def test_default_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert connections_path() == tmp_path / "inputs" / "linkedin" / "Connections.csv"
