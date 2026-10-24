"""A flaky source is retried; a run that was cut off is closed as interrupted (CH-164)."""

from __future__ import annotations

import json
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import RunLog
from careerhub.core.runlog import close_interrupted_runs, start_run
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import FetchError, approve_source, ingest_greenhouse, propose_source
from careerhub.modules.jobs.ingest import RETRY_WAIT_SECONDS, retryable_status

GOOD = {"jobs": [{"id": 1, "title": "Dev", "absolute_url": "https://g/1", "content": ""}]}


WAITS: list[float] = []


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    WAITS.clear()
    monkeypatch.setattr(
        "careerhub.modules.jobs.ingest.time", types.SimpleNamespace(sleep=WAITS.append)
    )
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def source(session: Session) -> object:
    return approve_source(
        session, propose_source(session, "greenhouse", "Acme", "u", {"board": "acme"}).id
    )


def client(answers: list[object]) -> tuple[httpx.Client, list[int]]:
    """A client that answers in order: an int is a status, an Exception is raised, a dict is OK."""
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        answer = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, content="{}")
        return httpx.Response(200, content=json.dumps(answer))

    return httpx.Client(transport=httpx.MockTransport(handle)), calls


def test_which_statuses_are_worth_trying_again() -> None:
    assert [retryable_status(c) for c in (200, 404, 403, 429, 500, 503)] == [
        False, False, False, True, True, True,
    ]  # fmt: skip


def test_a_server_error_then_success_is_retried_with_growing_waits(session: Session) -> None:
    waits = WAITS
    http, calls = client([503, 500, GOOD])
    report = ingest_greenhouse(session, source(session), http)
    assert report.new == 1 and len(calls) == 3
    assert waits == [RETRY_WAIT_SECONDS, RETRY_WAIT_SECONDS * 2]


def test_network_trouble_and_rate_limits_are_retried(session: Session) -> None:
    waits = WAITS
    http, calls = client([httpx.ConnectError("boom"), 429, GOOD])
    ingest_greenhouse(session, source(session), http)
    assert len(calls) == 3 and len(waits) == 2


@pytest.mark.parametrize("status", [404, 403, 401])
def test_a_client_error_is_not_retried(session: Session, status: int) -> None:
    waits = WAITS
    http, calls = client([status])
    with pytest.raises(FetchError) as raised:
        ingest_greenhouse(session, source(session), http)
    assert len(calls) == 1 and waits == [] and not raised.value.retryable


def test_an_unexpected_reply_is_not_retried(session: Session) -> None:
    http, calls = client([{"unexpected": True}])
    with pytest.raises(FetchError):
        ingest_greenhouse(session, source(session), http)
    assert len(calls) == 1


def test_a_source_that_keeps_failing_gives_up_after_the_retries_and_is_marked_failing(
    session: Session,
) -> None:
    src = source(session)
    http, calls = client([500])
    with pytest.raises(FetchError):
        ingest_greenhouse(session, src, http)
    assert len(calls) == 3  # one try and two retries (the default setting)
    assert src.health == "failing"  # type: ignore[attr-defined]


def test_the_number_of_retries_is_a_setting(session: Session) -> None:
    assert Settings().source_retries == 2
    with pytest.raises(ValueError):
        Settings(source_retries=9)


def test_runs_that_were_cut_off_are_closed_as_interrupted(session: Session) -> None:
    stuck = start_run(session, "nightly")
    other = start_run(session, "backup")
    assert close_interrupted_runs(session, "nightly") == 1
    session.refresh(stuck)
    session.refresh(other)
    assert (stuck.status, stuck.error_summary) == ("failed", "interrupted")
    assert stuck.finished_at is not None and other.status == "running"  # other steps untouched
    assert close_interrupted_runs(session, "nightly") == 0
    assert session.query(RunLog).count() == 2
