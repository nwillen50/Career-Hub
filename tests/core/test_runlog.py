from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.runlog import finish_run, recent_runs, start_run
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_a_started_run_is_saved_as_running(session: Session) -> None:
    run = start_run(session, "fetch")
    assert run.id is not None and run.status == "running" and run.finished_at is None


def test_finishing_records_the_outcome(session: Session) -> None:
    run = finish_run(session, start_run(session, "fetch"), items_processed=12)
    assert run.status == "succeeded" and run.items_processed == 12
    assert run.finished_at is not None and run.error_summary is None


def test_failure_stores_only_the_error_type(session: Session) -> None:
    run = start_run(session, "fetch")
    finish_run(session, run, error=ValueError("bad row for jane.doe@example.com"))
    assert run.status == "failed" and run.error_summary == "ValueError"


def test_recent_runs_newest_first_and_limited(session: Session) -> None:
    for step in ("a", "b", "c"):
        finish_run(session, start_run(session, step))
    assert [run.step for run in recent_runs(session, limit=2)] == ["c", "b"]
