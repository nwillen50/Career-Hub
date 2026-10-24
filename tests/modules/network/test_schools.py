"""Tests for same-school warm paths and the alumni search link (CH-101). All made up."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Person
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.network import import_alumni, warm_paths
from careerhub.modules.network.schools import (
    alumni_search_link,
    my_schools,
    same_school,
    school_key,
)
from careerhub.web.shortlist import ShortlistData, gather_shortlist, render_shortlist


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


@pytest.mark.parametrize(
    "variant",
    ["Tufts", "Tufts University", "Tufts Univ.", "the TUFTS university", "Tufts  University "],
)
def test_one_school_however_it_is_written(variant: str) -> None:
    assert school_key(variant) == school_key("Tufts University") == "tufts"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("MIT", "Massachusetts Institute of Technology"),
        ("UMass", "University of Massachusetts"),
        ("Georgia Tech", "Georgia Institute of Technology"),
        ("CMU", "Carnegie Mellon University"),
    ],
)
def test_common_abbreviations_are_one_school(a: str, b: str) -> None:
    assert school_key(a) == school_key(b)


def test_different_schools_are_not_merged() -> None:
    assert school_key("Tufts University") != school_key("Tufts Medical College")
    assert school_key("Boston College") != school_key("Boston University")
    assert school_key("Boston University") == school_key("Boston Univ.")


def test_user_aliases_join_schools() -> None:
    aliases = {"Tufts University": ["Jumbo U"]}
    assert school_key("Jumbo U", aliases) == school_key("Tufts", aliases)
    assert same_school(["Jumbo U"], ["Tufts University"], aliases) == "Jumbo U"
    assert same_school(["Brown"], ["Tufts University"], aliases) is None
    assert same_school([], ["Tufts"]) is None and same_school(["Tufts"], []) is None


def make_company(session: Session, name: str = "Acme") -> Company:
    company = Company(name=name, name_key=name.lower())
    session.add(company)
    session.flush()
    return company


def test_a_same_school_person_gets_the_reason_and_two_points(session: Session) -> None:
    company = make_company(session)
    session.add_all(
        [
            Person(full_name="Ada Same", company=company, schools=["Tufts Univ."]),
            Person(full_name="Ben Other", company=company, schools=["Brown University"]),
            Person(full_name="Cy None", company=company),
        ]
    )
    session.commit()
    paths = warm_paths(session, company.id, limit=5, schools=["Tufts University"])
    by_name = {p.name: p for p in paths}
    assert by_name["Ada Same"].strength == 2 and by_name["Ada Same"].same_school == "Tufts Univ."
    assert by_name["Ada Same"].reasons == ["Same school: Tufts Univ."]
    assert by_name["Ben Other"].same_school is None and by_name["Ben Other"].strength == 0
    assert paths[0].name == "Ada Same"


def test_an_alumni_list_member_for_the_same_school_is_not_counted_twice(session: Session) -> None:
    company = make_company(session)
    session.add(
        Person(
            full_name="Ada Same",
            company=company,
            schools=["Tufts"],
            how_known="alumni: Tufts Physics",
            origin="alumni_list",
        )
    )
    session.commit()
    path = warm_paths(session, company.id, schools=["Tufts University"])[0]
    assert path.strength == 2  # not 4
    assert "Same school: Tufts" in path.reasons and "alumni: Tufts Physics" in path.reasons
    other = warm_paths(session, company.id, schools=["Brown"])[0]
    assert other.strength == 2 and other.same_school is None  # the alumni tag alone still counts


def test_school_and_the_users_own_words_add_up(session: Session) -> None:
    company = make_company(session)
    session.add(Person(full_name="Ada", company=company, schools=["Tufts"], how_known="mentor"))
    session.commit()
    assert warm_paths(session, company.id, schools=["Tufts"])[0].strength == 5


def test_an_import_with_a_school_column_records_the_school(
    tmp_path: Path, session: Session
) -> None:
    path = tmp_path / "a.csv"
    path.write_text(
        "Name,Company,University,Group\nAda,Acme,Tufts University,Club\nBen,Acme,,Club\n",
        encoding="utf-8",
    )
    import_alumni(session, path)
    import_alumni(session, path)  # again: no duplicate school
    people = {p.full_name: p for p in session.scalars(select(Person))}
    assert people["Ada"].schools == ["Tufts University"] and people["Ben"].schools is None


def test_tagging_with_a_school_keeps_the_words_and_does_not_repeat_a_school(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    company = make_company(session)
    person = Person(full_name="Ada", company=company, how_known="mentor")
    session.add(person)
    session.commit()
    store = InMemoryKeyStore()
    from careerhub.modules.network import tag_person

    tag_person(session, person.id, "", school="Tufts University")
    tag_person(session, person.id, "", school="tufts")
    assert person.schools == ["Tufts University"] and person.how_known == "mentor"
    tag_person(session, person.id, "former teammate")
    assert person.how_known == "former teammate"
    assert store.get("x") is None and main(["people", "tag", "9", "--school", "X", "y"], store) in (
        1,
        0,
    )


def test_my_schools_come_from_profile_md(session: Session, tmp_path: Path) -> None:
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.md").write_text(
        "---\nname: Jane\neducation:\n  - {school: Tufts University, degree: BS}\n---\n"
    )
    assert my_schools(session) == ["Tufts University"]
    assert my_schools(None) == ["Tufts University"]


def test_the_alumni_link_is_built_from_the_slug_and_the_company_and_never_fetched() -> None:
    link = alumni_search_link("tufts-university", "Acme & Sons, Inc.")
    assert (
        link
        == "https://www.linkedin.com/school/tufts-university/people/?keywords=Acme%20%26%20Sons%2C%20Inc."
    )
    assert alumni_search_link(None, "Acme") is None
    assert alumni_search_link("bad slug/../x", "Acme") is None
    assert Settings().linkedin_school_slug is None
    with pytest.raises(ValueError):
        Settings(linkedin_school_slug="bad slug")


def test_the_page_marks_same_school_people_and_offers_the_link(
    session: Session, tmp_path: Path
) -> None:
    from careerhub.core.models import Assessment, Job, Source
    from careerhub.modules.jobs import NormalizedJob, store_jobs

    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.md").write_text(
        "---\nname: Jane\neducation:\n  - {school: Tufts University, degree: BS}\n---\n"
    )
    (tmp_path / "settings.json").write_text(
        Settings(linkedin_school_slug="tufts-university").model_dump_json()
    )
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Engineer", "u", "Boston")])
    job = session.scalars(select(Job)).one()
    session.add(Assessment(job_id=job.id, filter_passed=True, fit_score=80.0, explanation="x"))
    session.add(Person(full_name="Ada Same", company_id=job.company_id, schools=["Tufts"]))
    session.commit()
    entry = gather_shortlist(session).entries[0]
    assert entry.contacts[0].same_school == "Tufts"
    html = render_shortlist(ShortlistData([entry]), datetime.now(UTC))
    assert "Same school</span>" in html
    assert 'href="https://www.linkedin.com/school/tufts-university/people/?keywords=Acme"' in html
    assert (
        "Find Tufts University alumni at Acme on LinkedIn" in html
        and "Career Hub doesn't visit it" in html
    )


def test_branch_campus_and_system_contacts_rank_below_the_same_school(session: Session) -> None:
    """CH-179: same school, then branch campus (weaker), then same system (weaker still)."""
    company = make_company(session)
    session.add_all(
        [
            Person(full_name="A System", company=company, schools=["UC Davis"]),
            Person(full_name="B Branch", company=company, schools=["Penn State Behrend"]),
            Person(full_name="C Same", company=company, schools=["Pennsylvania State University"]),
            Person(full_name="D Other", company=company, schools=["Brown University"]),
        ]
    )
    session.commit()
    mine = ["Penn State", "UCLA"]
    paths = warm_paths(session, company.id, limit=5, schools=mine)
    assert [p.name for p in paths] == ["C Same", "B Branch", "A System", "D Other"]
    by_name = {p.name: p for p in paths}
    assert by_name["C Same"].strength == 2 and by_name["C Same"].school_tie is None
    assert by_name["B Branch"].strength == 1
    assert by_name["B Branch"].school_tie == "Branch campus: Penn State"
    assert by_name["B Branch"].same_school is None
    assert by_name["A System"].strength == 0
    assert by_name["A System"].reasons == ["Same system: University of California"]
    assert by_name["D Other"].school_tie is None


def test_profile_show_lists_an_ambiguous_school_and_how_to_settle_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from careerhub.cli import _ambiguous_schools_note

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.md").write_text(
        "---\nname: Jane\neducation:\n  - {school: PSU, degree: BS}\n---\n", encoding="utf-8"
    )
    note = _ambiguous_schools_note()
    assert "'PSU' could mean" in note and "Portland State University" in note
    assert "school_aliases" in note
