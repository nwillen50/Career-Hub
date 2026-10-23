from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Person
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.network import UnknownPerson, find_people, import_alumni, tag_person
from careerhub.profile import ProfileFileError

HEADER = "Name,Company,Title,Group,Email,URL\n"
ADA = (
    "Ada Sample,Acme Corp,Engineer,Example University,ada@example.com,https://example.com/in/ada\n"
)
BEN = "Ben Example,Globex,Analyst,Robotics Club,,\n"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def write(tmp_path: Path, text: str, name: str = "alumni.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_alumni_are_added_with_company_and_tag(tmp_path: Path, session: Session) -> None:
    report = import_alumni(session, write(tmp_path, HEADER + ADA + BEN))
    assert (report.created, report.skipped) == (2, 0)
    ada = session.scalars(select(Person).where(Person.full_name == "Ada Sample")).one()
    assert ada.origin == "alumni_list" and ada.how_known == "alumni: Example University"
    assert ada.company is not None and ada.company.name == "Acme Corp" and ada.title == "Engineer"


def test_reimport_changes_nothing(tmp_path: Path, session: Session) -> None:
    path = write(tmp_path, HEADER + ADA + BEN)
    import_alumni(session, path)
    again = import_alumni(session, path)
    assert (again.created, again.tagged, again.unchanged) == (0, 0, 2)
    assert len(session.scalars(select(Person)).all()) == 2


def test_an_existing_connection_gets_the_tag_added_without_losing_the_users_words(
    tmp_path: Path, session: Session
) -> None:
    company = Company(name="Acme Corp", name_key="acme")
    known = Person(
        full_name="ada sample",
        company=company,
        origin="linkedin_export",
        how_known="former teammate",
    )
    session.add_all([company, known])
    session.commit()
    report = import_alumni(session, write(tmp_path, HEADER + ADA))
    assert (report.created, report.tagged) == (0, 1)
    session.refresh(known)
    assert known.how_known == "former teammate; alumni: Example University"
    assert known.origin == "linkedin_export" and len(session.scalars(select(Person)).all()) == 1


def test_matching_by_email_or_url_across_name_spellings(tmp_path: Path, session: Session) -> None:
    session.add(Person(full_name="Augusta Ada Sample", email="ADA@example.com"))
    session.commit()
    report = import_alumni(session, write(tmp_path, HEADER + ADA))
    assert report.tagged == 1 and len(session.scalars(select(Person)).all()) == 1


def test_rows_missing_required_values_are_skipped(tmp_path: Path, session: Session) -> None:
    rows = ",Acme,Eng,Club,,\nCy Free,,Eng,Club,,\nDee Good,Acme,Eng,,,\n" + BEN
    report = import_alumni(session, write(tmp_path, HEADER + rows))
    assert (report.created, report.skipped) == (1, 3)


def test_first_and_last_name_columns_work_and_case_does_not_matter(
    tmp_path: Path, session: Session
) -> None:
    text = "FIRST NAME,last name,COMPANY,group\nEve,Sample,Acme,Club\n"
    assert import_alumni(session, write(tmp_path, text)).created == 1
    assert session.scalars(select(Person)).one().full_name == "Eve Sample"


def test_a_file_without_the_needed_columns_is_an_error(tmp_path: Path, session: Session) -> None:
    with pytest.raises(ProfileFileError, match="Group"):
        import_alumni(session, write(tmp_path, "Name,Company\nA,B\n"))
    with pytest.raises(ProfileFileError):
        import_alumni(session, tmp_path / "missing.csv")


def test_find_and_tag_people(session: Session) -> None:
    company = Company(name="Acme Corp", name_key="acme")
    person = Person(full_name="Ada Sample", company=company)
    session.add_all([company, person])
    session.commit()
    assert [p.full_name for p in find_people(session, "acme")] == ["Ada Sample"]
    assert [p.full_name for p in find_people(session, "SAMPLE")] == ["Ada Sample"]
    assert find_people(session, "nobody") == []
    assert tag_person(session, person.id, "  former manager ").how_known == "former manager"
    assert tag_person(session, person.id, " ").how_known is None
    with pytest.raises(UnknownPerson):
        tag_person(session, 999, "x")


def test_hub_people_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    csv_path = write(tmp_path, HEADER + ADA)
    assert main(["people", "import-alumni", str(csv_path)], store) == 0
    assert "1 added" in capsys.readouterr().out
    assert main(["people", "find", "ada"], store) == 0
    assert "Ada Sample" in capsys.readouterr().out
    assert main(["people", "tag", "1", "mentor", "from", "club"], store) == 0
    assert "mentor from club" in capsys.readouterr().out
    assert main(["people", "tag", "9", "x"], store) == 1


def test_reimport_with_non_ascii_capitals_creates_no_duplicate(
    tmp_path: Path, session: Session
) -> None:
    """CH-152: SQLite lower() folds only ASCII, so 'Émile' once never matched itself."""
    path = write(tmp_path, HEADER + "Émile  Zola,Acme Corp,,Example University,,\n")
    import_alumni(session, path)
    again = import_alumni(session, path)
    assert again.created == 0 and again.unchanged == 1
    assert len(session.scalars(select(Person)).all()) == 1


def test_placeholder_emails_and_links_do_not_merge_people(tmp_path: Path, session: Session) -> None:
    """CH-153: 'N/A', '-' and a shared office address are not identities."""
    rows = (
        "Ada Sample,Acme Corp,,Example University,N/A,-\n"
        "Ben Example,Globex,,Example University,N/A,-\n"
        "Cam Other,Initech,,Example University,info@school.edu,\n"
        "Dee Person,Hooli,,Example University,info@school.edu,\n"
    )
    report = import_alumni(session, write(tmp_path, HEADER + rows))
    assert report.created == 4
    assert {p.email for p in session.scalars(select(Person))} == {None, "info@school.edu"}


def test_semicolon_in_a_group_name_stays_one_tag(tmp_path: Path, session: Session) -> None:
    """CH-153: ';' splits notes, so a group like 'Tufts; Class of 2020' broke re-import."""
    path = write(tmp_path, HEADER + "Ada Sample,Acme Corp,,Tufts; Class of 2020,,\n")
    import_alumni(session, path)
    again = import_alumni(session, path)
    assert again.unchanged == 1 and again.tagged == 0
    person = session.scalars(select(Person)).one()
    assert person.how_known == "alumni: Tufts, Class of 2020"


def test_relatives_sharing_an_email_stay_two_people(tmp_path: Path, session: Session) -> None:
    """Review: 'John Smith' and 'Jane Smith' share a family address but are not one person."""
    rows = (
        "John Smith,Acme Corp,,Example University,smiths@example.com,\n"
        "Jane Smith,Globex,,Example University,smiths@example.com,\n"
    )
    report = import_alumni(session, write(tmp_path, HEADER + rows))
    assert report.created == 2
