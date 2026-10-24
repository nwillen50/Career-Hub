"""Tests for company name matching and the key migration (CH-86)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from careerhub.core.companies import find_companies, get_or_create_company
from careerhub.core.migrate import _config, migrate
from careerhub.core.models import Company, Person, Source, utcnow
from careerhub.core.names import company_key
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.network import find_company


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


@pytest.mark.parametrize(
    "name",
    [
        "Microsoft",
        "Microsoft.com",
        "Microsoft Corporation",
        "Microsoft Corp.",
        "MICROSOFT, Inc.",
        "www.microsoft.com",
        "The Microsoft Company",
        "  microsoft  ",
    ],
)
def test_the_microsoft_variants_share_one_key(name: str) -> None:
    assert company_key(name) == "microsoft"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("AT&T", "AT and T"),
        ("The Walt Disney Company", "Disney"),
        ("Facebook", "Meta Platforms"),
        ("Alphabet", "Google LLC"),
        ("Salesforce.com, Inc.", "Salesforce"),
        ("Ben & Jerry's", "Ben and Jerrys"),
    ],
)
def test_other_spellings_and_built_in_aliases_match(a: str, b: str) -> None:
    assert company_key(a) == company_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [("Meta", "Metabase"), ("Acme Labs", "Acme Robotics"), ("Apple", "Apple Hospitality")],
)
def test_different_companies_are_never_merged(a: str, b: str) -> None:
    assert company_key(a) != company_key(b)


@pytest.mark.parametrize("name", ["", "   ", "Inc", "The", ".com", "Co", "&", "...", "Corp. Inc."])
def test_odd_names_do_not_crash_and_are_stable(name: str) -> None:
    key = company_key(name)
    assert company_key(key) == key  # idempotent


def test_get_or_create_treats_variants_as_one_company(session: Session) -> None:
    first = get_or_create_company(session, "Microsoft Corporation")
    again = get_or_create_company(session, "Microsoft.com")
    assert first is not None and again is first and again.name == "Microsoft Corporation"
    assert get_or_create_company(session, "  ") is None


def test_postings_from_two_sources_share_the_company(session: Session) -> None:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Microsoft", "1", "Engineer", "u", "WA", "t")])
    store_jobs(session, source, [NormalizedJob("Microsoft Corp.", "2", "Analyst", "u", "WA", "t")])
    assert len(session.scalars(select(Company)).all()) == 1


def test_a_long_name_finds_the_short_company_and_meta_does_not_find_metabase(
    session: Session,
) -> None:
    get_or_create_company(session, "Microsoft")
    get_or_create_company(session, "Metabase")
    session.commit()
    assert find_company(session, "Microsoft Corporation").name == "Microsoft"  # type: ignore[union-attr]
    assert find_companies(session, "Meta") == []


def test_several_whole_word_matches_are_reported_not_picked(session: Session) -> None:
    for name in ("Acme Labs", "Acme Robotics", "Acmeville"):
        get_or_create_company(session, name)
    session.commit()
    assert [c.name for c in find_companies(session, "Acme")] == ["Acme Labs", "Acme Robotics"]
    assert find_company(session, "Acme") is None  # ambiguous
    assert find_company(session, "Acme Labs").name == "Acme Labs"  # type: ignore[union-attr]
    assert find_companies(session, "") == []


def test_the_migration_recomputes_keys_and_merges_duplicates(tmp_path: Path) -> None:
    engine = open_engine(tmp_path / "old.db", new_database_key())
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "e8414035bd90")  # the revision before the key rules changed
    with session_factory(engine)() as session:
        first = Company(name="Microsoft", name_key="microsoft", domains=["microsoft.com"])
        second = Company(
            name="Microsoft Corporation",
            name_key="microsoft corporation",
            domains=["msft.com"],
            ats_board_ids={"greenhouse": "msft"},
        )
        other = Company(name="Metabase", name_key="metabase")
        session.add_all([first, second, other])
        session.flush()
        # Core inserts name their columns: the old schema lacks later ones (person.phone).
        now = utcnow()
        for name, company_id in (("Ada", second.id), ("Bo", None)):
            session.execute(
                insert(Person.__table__).values(
                    full_name=name, company_id=company_id, created_at=now, updated_at=now
                )
            )
        session.commit()
    migrate(engine)
    with session_factory(engine)() as session:
        companies = {c.name_key: c for c in session.scalars(select(Company))}
        assert sorted(companies) == ["metabase", "microsoft"]
        kept = companies["microsoft"]
        assert kept.name == "Microsoft"  # the earliest row is kept
        assert kept.domains == ["microsoft.com", "msft.com"]
        assert kept.ats_board_ids == {"greenhouse": "msft"}
        ada = session.scalars(select(Person).where(Person.full_name == "Ada")).one()
        assert ada.company_id == kept.id


def old_database(tmp_path: Path, companies: list[Company]):  # type: ignore[no-untyped-def]
    engine = open_engine(tmp_path / "old.db", new_database_key())
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "e8414035bd90")
    with session_factory(engine)() as session:
        session.add_all(companies)
        session.commit()
    return engine


def test_the_migration_survives_a_new_key_equal_to_another_rows_old_key(tmp_path: Path) -> None:
    engine = old_database(
        tmp_path,
        [Company(name="Foo-Inc", name_key="foo-inc"), Company(name="Foo Inc", name_key="foo inc")],
    )
    migrate(engine)  # used to raise IntegrityError on the unique name_key
    with session_factory(engine)() as session:
        assert [c.name_key for c in session.scalars(select(Company))] == ["foo inc", "foo"]


def test_merging_keeps_the_notes_size_and_industry_of_the_dropped_company(tmp_path: Path) -> None:
    engine = old_database(
        tmp_path,
        [
            Company(name="Microsoft", name_key="microsoft", notes="mine"),
            Company(
                name="Microsoft Corporation",
                name_key="microsoft corporation",
                notes="my manager is Ada",
                size="10000+",
                industry="Software",
            ),
        ],
    )
    migrate(engine)
    with session_factory(engine)() as session:
        kept = session.scalars(select(Company)).one()
        assert kept.notes == "mine\nmy manager is Ada"
        assert (kept.size, kept.industry) == ("10000+", "Software")


def test_merge_companies_also_keeps_notes(session: Session) -> None:
    from careerhub.core.companies import merge_companies

    keep, drop = (
        Company(name="A", name_key="a"),
        Company(name="B", name_key="b", notes="n", size="5"),
    )
    session.add_all([keep, drop])
    session.flush()
    merge_companies(session, keep, drop)
    assert (keep.notes, keep.size) == ("n", "5")


def test_the_same_job_at_two_sources_links_whatever_the_company_spelling() -> None:
    from careerhub.modules.jobs import dedupe_key

    assert dedupe_key("Microsoft Corp.", "Engineer", "WA") == dedupe_key(
        "Microsoft", "Engineer", "WA"
    )


def test_the_migration_recomputes_the_dedupe_keys_of_stored_jobs(tmp_path: Path) -> None:
    from careerhub.core.models import Job
    from careerhub.modules.jobs import dedupe_key

    engine = old_database(tmp_path, [Company(name="Microsoft Corp.", name_key="microsoft corp.")])
    with session_factory(engine)() as session:
        company_id = session.scalars(select(Company.id)).one()  # old schema: id only
        source = Source(kind="greenhouse", name="s", url="u", status="approved")
        session.add(source)
        session.flush()
        session.add(
            Job(
                company_id=company_id,
                source_id=source.id,
                external_id="1",
                title="Engineer",
                url="u",
                location="WA",
                dedupe_key="stale",
                first_seen_at=utcnow(),
                last_seen_at=utcnow(),
            )
        )
        session.commit()
    migrate(engine)
    with session_factory(engine)() as session:
        assert session.scalars(select(Job)).one().dedupe_key == dedupe_key(
            "Microsoft", "Engineer", "WA"
        )


def test_short_names_keep_their_endings_so_different_companies_stay_apart() -> None:
    """CH-134: stripping '.ai' and 'Corp' both left 'x', merging two companies."""
    assert company_key("X.ai") != company_key("X Corp")
    assert company_key("X.ai") == "xai" and company_key("X Corp") == "x"
    # legal words still drop, so keys of names already stored do not change
    assert company_key("HP Inc") == company_key("HP") == "hp"
    assert company_key("3M Co") == company_key("3M") == "3m"
    assert company_key("Acme Corp") == "acme"  # longer names still lose their endings
