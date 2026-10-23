"""An accepted company merge stays merged (CH-154)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.companies import (
    decide_merge,
    find_companies,
    get_or_create_company,
    pending_merges,
    suggest_merges,
)
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def merged(session: Session) -> Company:
    """Create Acme and Acme Robotics, accept the suggested merge, and return Acme."""
    acme = get_or_create_company(session, "Acme")
    assert acme and get_or_create_company(session, "Acme Robotics")
    session.commit()
    suggest_merges(session)
    (item,) = pending_merges(session)
    decide_merge(session, item.id, accept=True)
    return acme


def source(session: Session, name: str) -> Source:
    row = Source(kind="greenhouse", name=name, url=f"https://x/{name}", status="approved")
    session.add(row)
    session.flush()
    return row


def test_the_dropped_name_still_finds_the_kept_company(session: Session) -> None:
    acme = merged(session)
    assert get_or_create_company(session, "Acme Robotics Inc.") is acme
    assert find_companies(session, "Acme Robotics") == [acme]
    assert len(session.scalars(select(Company)).all()) == 1


def test_a_later_ingest_under_the_old_name_does_not_split_the_company_again(
    session: Session,
) -> None:
    acme = merged(session)
    s = source(session, "gh")
    store_jobs(session, s, [NormalizedJob("Acme Robotics", "1", "Engineer", "u", "Boston", "t")])
    assert {j.company_id for j in session.scalars(select(Job))} == {acme.id}
    assert suggest_merges(session) == 0


def test_postings_of_one_job_from_two_sources_are_linked_after_a_merge(session: Session) -> None:
    get_or_create_company(session, "Acme")
    s1, s2 = source(session, "gh"), source(session, "lever")
    store_jobs(session, s1, [NormalizedJob("Acme", "1", "Engineer", "u1", "Boston", "t")])
    store_jobs(session, s2, [NormalizedJob("Acme Robotics", "2", "Engineer", "u2", "Boston", "t")])
    suggest_merges(session)
    (item,) = pending_merges(session)
    decide_merge(session, item.id, accept=True)
    first, second = session.scalars(select(Job).order_by(Job.id)).all()
    assert first.dedupe_key == second.dedupe_key
    assert first.duplicate_of_id is None and second.duplicate_of_id == first.id


def test_a_suggestion_whose_ids_now_name_other_companies_merges_nothing(session: Session) -> None:
    get_or_create_company(session, "Acme")
    robotics = get_or_create_company(session, "Acme Robotics")
    assert robotics
    session.commit()
    suggest_merges(session)
    (item,) = pending_merges(session)
    # the dropped company vanishes and another company takes over its id
    session.delete(robotics)
    session.commit()
    session.add(Company(id=robotics.id, name="Globex", name_key="globex"))
    session.commit()
    assert "already gone" in decide_merge(session, item.id, accept=True)
    assert get_or_create_company(session, "Globex") is not None
    assert len(session.scalars(select(Company)).all()) == 2


def test_merged_jobs_get_the_same_duplicate_key_ingest_would_give_them(session: Session) -> None:
    """Review: the key must apply the user's aliases, or later duplicates stop linking."""
    from careerhub.modules.jobs import dedupe_key

    aliases = {"acme": "acme labs"}
    get_or_create_company(session, "Acme", aliases)
    s1 = source(session, "gh")
    store_jobs(session, s1, [NormalizedJob("Acme Robotics", "1", "Engineer", "u", "Boston", "t")])
    robotics = get_or_create_company(session, "Acme Robotics")
    keep = get_or_create_company(session, "Acme", aliases)
    assert robotics and keep
    from careerhub.core.companies import merge_companies

    merge_companies(session, keep, robotics, aliases)
    job = session.scalars(select(Job)).one()
    assert job.dedupe_key == dedupe_key("Acme", "Engineer", "Boston", aliases)
