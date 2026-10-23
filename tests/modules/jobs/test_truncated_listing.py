"""A listing that shrinks sharply is not trusted to close the rest (CH-132, ADR-0035)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_closed_vs_glitch import Board, approved

from careerhub.core.migrate import migrate
from careerhub.core.models import Job
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import ingest_greenhouse


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


BIG = {
    "jobs": [
        {"id": i, "title": f"Role {i}", "absolute_url": f"https://g/{i}", "content": ""}
        for i in range(1, 11)
    ]
}
CUT = {"jobs": BIG["jobs"][:2]}  # the feed was cut off after two jobs


def open_count(session: Session) -> int:
    return len([j for j in session.scalars(select(Job)) if j.status != "closed"])


def test_a_cut_off_listing_keeps_the_missing_jobs_open_when_they_are_still_there(
    session: Session,
) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(BIG).client())
    board = Board(CUT, per_job={str(i): 200 for i in range(3, 11)})
    report = ingest_greenhouse(session, source, board.client())
    assert open_count(session) == 10 and report.closed == 0
    assert report.note and "kept" in report.note


def test_a_cut_off_listing_with_no_per_job_check_keeps_jobs_open(
    session: Session,
) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(BIG).client())
    board = Board(CUT, per_job={str(i): 500 for i in range(3, 11)})  # the check can't tell
    report = ingest_greenhouse(session, source, board.client())
    assert open_count(session) == 10 and report.closed == 0


def test_a_big_drop_closes_jobs_when_the_missing_ones_are_really_gone(
    session: Session,
) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(BIG).client())
    report = ingest_greenhouse(session, source, Board(CUT).client())  # per-job: 404
    assert open_count(session) == 2 and report.closed == 8


def test_a_small_drop_closes_normally_without_any_check(session: Session) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(BIG).client())
    board = Board({"jobs": BIG["jobs"][:7]})  # 3 of 10 gone: under the share
    report = ingest_greenhouse(session, source, board.client())
    assert report.closed == 3 and not [p for p in board.requests if "/jobs/" in p]


def test_the_share_is_a_setting(session: Session) -> None:
    assert Settings().listing_drop_share == 0.5
