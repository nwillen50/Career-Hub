"""Alumni import: row limit, UTF-16, school keys, placeholders, kept tags (CH-155)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Person
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.network import import_alumni, layouts, tag_person
from careerhub.modules.network.layouts import read_table
from careerhub.modules.network.schools import school_key

HEADER = "Name,Company,Group\n"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_rows_beyond_the_limit_are_reported_not_silently_dropped(
    tmp_path: Path, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(layouts, "MAX_ROWS", 3)
    path = tmp_path / "big.csv"
    path.write_text(
        HEADER + "".join(f"Person {i},Acme,Club\n" for i in range(10)), encoding="utf-8"
    )
    table = read_table(path)
    assert table.truncated and len(table.rows) == 2  # the limit counts the header row too
    assert import_alumni(session, path).truncated
    small = tmp_path / "small.csv"
    small.write_text(HEADER + "Ada Sample,Acme,Club\n", encoding="utf-8")
    assert not import_alumni(session, small).truncated


def test_blank_formatted_excel_rows_do_not_use_up_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openpyxl

    monkeypatch.setattr(layouts, "MAX_ROWS", 3)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Name", "Company", "Group"])
    for _ in range(20):
        sheet.append([None, None, None])  # formatted but empty
    sheet.append(["Ada Sample", "Acme", "Club"])
    path = tmp_path / "a.xlsx"
    workbook.save(path)
    table = read_table(path)
    assert not table.truncated and table.rows == [["Ada Sample", "Acme", "Club"]]


def test_a_utf16_tab_separated_export_is_read(tmp_path: Path, session: Session) -> None:
    """Excel's "Unicode Text" save is UTF-16 with tabs."""
    path = tmp_path / "unicode.txt.tsv"
    path.write_bytes(("Name\tCompany\tGroup\nÉmile Zola\tAcme\tClub\n").encode("utf-16"))
    report = import_alumni(session, path)
    assert report.created == 1
    assert session.scalars(select(Person)).one().full_name == "Émile Zola"
    bare = tmp_path / "le.tsv"
    bare.write_bytes(("Name\tCompany\tGroup\nAda Sample\tAcme\tClub\n").encode("utf-16-le"))
    assert import_alumni(session, bare).created == 1


def test_university_of_x_and_x_university_are_not_one_school_where_they_are_two() -> None:
    assert school_key("University of Washington") != school_key("Washington University")
    assert school_key("University of Miami") != school_key("Miami University")
    assert school_key("Tufts") == school_key("Tufts University") == school_key("Univ. of Tufts")
    assert school_key("University of Washington") == school_key("Washington")


def test_tagging_a_person_keeps_their_alumni_tags(session: Session) -> None:
    person = Person(full_name="Ada Sample", how_known="alumni: Tufts; former manager")
    session.add(person)
    session.commit()
    assert tag_person(session, person.id, "mentor").how_known == "mentor; alumni: Tufts"
    assert tag_person(session, person.id, "").how_known == "alumni: Tufts"
    loose = Person(full_name="Ben", how_known="old note")
    session.add(loose)
    session.commit()
    assert tag_person(session, loose.id, " ").how_known is None


def test_placeholder_employers_are_skipped_not_pooled_into_one_company(
    tmp_path: Path, session: Session
) -> None:
    path = tmp_path / "a.csv"
    path.write_text(
        HEADER + "Ada Sample,N/A,Club\nBen Example,-,Club\nCy Other,Acme,Club\n", encoding="utf-8"
    )
    report = import_alumni(session, path)
    assert (report.created, report.skipped, report.skipped_for) == (1, 2, ("company",))
    assert [c.name for c in session.scalars(select(Company))] == ["Acme"]


def test_rows_with_no_group_are_named_in_the_report(tmp_path: Path, session: Session) -> None:
    path = tmp_path / "a.csv"
    path.write_text("Name,Company,Group\nAda Sample,Acme,\n", encoding="utf-8")
    assert import_alumni(session, path).skipped_for == ("group",)


def test_the_university_of_x_keeps_its_key_with_a_leading_the() -> None:
    """Review: 'The University of Washington' must not turn into 'Washington University'."""
    assert school_key("The University of Washington") == school_key("University of Washington")
    assert school_key("The University of Miami") != school_key("Miami University")
    assert school_key("The Washington University") == school_key("Washington University")


def test_a_utf32_export_is_read(tmp_path: Path, session: Session) -> None:
    path = tmp_path / "u32.tsv"
    path.write_bytes("Name\tCompany\tGroup\nAda Sample\tAcme\tClub\n".encode("utf-32"))
    assert import_alumni(session, path).created == 1


def test_the_truncation_notice_prints_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from careerhub.cli import main
    from careerhub.core.secrets import InMemoryKeyStore

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(layouts, "MAX_ROWS", 3)
    path = tmp_path / "big.csv"
    path.write_text(HEADER + "".join(f"P{i},Acme,Club\n" for i in range(8)), encoding="utf-8")
    assert main(["alumni", "import", str(path)], InMemoryKeyStore()) == 0
    assert capsys.readouterr().out.count("more rows than Career Hub reads") == 1
