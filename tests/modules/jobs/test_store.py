from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, dedupe_key, store_jobs

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def make_source(session: Session, url: str = "https://example.com/a") -> Source:
    source = Source(kind="greenhouse", name=url, url=url, status="approved")
    session.add(source)
    session.commit()
    return source


def posting(
    external_id: str = "1", title: str = "Software Engineer", **kw: object
) -> NormalizedJob:
    return NormalizedJob(
        company_name=str(kw.get("company", "Acme")),
        external_id=external_id,
        title=title,
        url=f"https://example.com/jobs/{external_id}",
        location="Boston, MA",
        raw_text="About the job",
        raw_payload={"id": external_id},
    )


def test_new_postings_are_saved_with_their_company_and_raw_data(session: Session) -> None:
    source = make_source(session)
    report = store_jobs(session, source, [posting("1"), posting("2")], T0)
    assert report.new == 2
    job = session.scalars(select(Job).where(Job.external_id == "1")).one()
    assert job.company.name == "Acme" and job.status == "new"
    assert job.raw_payload == {"id": "1"} and job.first_seen_at == T0


def test_a_rerun_adds_no_duplicates_and_refreshes_last_seen(session: Session) -> None:
    source = make_source(session)
    store_jobs(session, source, [posting("1")], T0)
    later = T0 + timedelta(days=1)
    report = store_jobs(session, source, [posting("1", title="Software Engineer II")], later)
    assert (report.new, report.seen_again) == (0, 1)
    job = session.scalars(select(Job)).one()
    assert job.title == "Software Engineer II"
    assert (job.first_seen_at, job.last_seen_at) == (T0, later)
    assert len(session.scalars(select(Company)).all()) == 1


def test_postings_missing_from_a_full_fetch_are_closed_not_deleted(session: Session) -> None:
    source = make_source(session)
    store_jobs(session, source, [posting("1"), posting("2")], T0)
    report = store_jobs(session, source, [posting("1")], T0 + timedelta(days=1))
    assert report.closed == 1
    statuses = {j.external_id: j.status for j in session.scalars(select(Job))}
    assert statuses == {"1": "new", "2": "closed"}


def test_a_returning_posting_is_reopened(session: Session) -> None:
    source = make_source(session)
    store_jobs(session, source, [posting("1")], T0)
    store_jobs(session, source, [], T0 + timedelta(days=1))
    report = store_jobs(session, source, [posting("1")], T0 + timedelta(days=2))
    assert report.reopened == 1 and session.scalars(select(Job)).one().status == "new"


def test_the_same_job_from_another_source_is_linked_to_the_first(session: Session) -> None:
    first, second = make_source(session), make_source(session, "https://example.com/b")
    store_jobs(session, first, [posting("1")], T0)
    report = store_jobs(
        session, second, [posting("99", company="ACME ", title="software  engineer")], T0
    )
    assert report.duplicates == 1
    original = session.scalars(select(Job).where(Job.source_id == first.id)).one()
    copy = session.scalars(select(Job).where(Job.source_id == second.id)).one()
    assert copy.duplicate_of_id == original.id and original.duplicate_of_id is None


def test_different_titles_are_not_duplicates(session: Session) -> None:
    first, second = make_source(session), make_source(session, "https://example.com/b")
    store_jobs(session, first, [posting("1")], T0)
    assert store_jobs(session, second, [posting("2", title="Data Analyst")], T0).duplicates == 0


def test_the_same_posting_twice_at_one_source_is_refused_by_the_database(session: Session) -> None:
    source = make_source(session)
    store_jobs(session, source, [posting("1")], T0)
    session.add(
        Job(
            company_id=1,
            source_id=source.id,
            external_id="1",
            title="x",
            url="u",
            first_seen_at=T0,
            last_seen_at=T0,
            dedupe_key="k",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_dedupe_key_ignores_case_spacing_and_punctuation() -> None:
    assert dedupe_key("Acme, Inc.", "Software  Engineer", "Boston, MA") == dedupe_key(
        "acme inc", "software engineer", "boston ma"
    )
