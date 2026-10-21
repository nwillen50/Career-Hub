from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import _ingest, main
from careerhub.core.migrate import migrate
from careerhub.core.models import Job, Source
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import (
    FetchError,
    SourceNotApproved,
    approve_source,
    fetch_greenhouse,
    ingest_greenhouse,
    propose_source,
)
from careerhub.modules.jobs.greenhouse import html_to_text

# A made-up board in the shape Greenhouse's public API returns.
BOARD: dict[str, Any] = {
    "jobs": [
        {
            "id": 101,
            "title": "Software Engineer, New Grad",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
            "location": {"name": "Boston, MA"},
            "content": (
                "&lt;p&gt;Build things.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
            ),
            "updated_at": "2026-10-01T10:00:00-04:00",
        },
        {
            "id": 102,
            "title": "Data Analyst",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
            "location": {"name": "Remote"},
            "company_name": "Acme Robotics",
        },
    ],
    "meta": {"total": 2},
}
URL = "https://boards.greenhouse.io/acme"


def client(reply: Any, status: int = 200, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(status, content=json.dumps(reply) if status == 200 else "echo")

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def approved(session: Session) -> Source:
    source = propose_source(session, "greenhouse", "Acme", URL, {"board": "acme"})
    return approve_source(session, source.id)


def test_fetch_normalizes_the_board(session: Session) -> None:
    seen: list[httpx.Request] = []
    jobs = fetch_greenhouse(approved(session), client(BOARD, seen=seen))
    assert str(seen[0].url) == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    first, second = jobs
    assert (first.external_id, first.location, first.company_name) == ("101", "Boston, MA", "Acme")
    assert first.raw_text == "Build things.\nPython" and first.raw_payload["id"] == 101
    assert second.company_name == "Acme Robotics" and second.raw_text is None


def test_an_unapproved_source_is_never_fetched(session: Session) -> None:
    source = propose_source(session, "greenhouse", "Acme", URL, {"board": "acme"})
    seen: list[httpx.Request] = []
    with pytest.raises(SourceNotApproved):
        fetch_greenhouse(source, client(BOARD, seen=seen))
    assert seen == []


def test_ingest_stores_jobs_and_a_rerun_adds_none(session: Session) -> None:
    """Step 4's acceptance test: postings are in the database and a rerun adds no duplicates."""
    source = approved(session)
    first = ingest_greenhouse(session, source, client(BOARD))
    again = ingest_greenhouse(session, source, client(BOARD))
    assert (first.new, again.new, again.seen_again) == (2, 0, 2)
    assert len(session.scalars(select(Job)).all()) == 2
    assert source.health == "ok" and source.last_fetched_at is not None


@pytest.mark.parametrize(
    "bad",
    [
        (httpx.ConnectError("down"), 200),
        ({"jobs": []}, 503),
        ({"nope": 1}, 200),
        ({"jobs": [{"id": 1}]}, 200),
    ],
)
def test_a_failed_fetch_changes_nothing_and_marks_the_source_failing(
    session: Session, bad: tuple[Any, int]
) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, client(BOARD))
    with pytest.raises(FetchError) as raised:
        ingest_greenhouse(session, source, client(bad[0], bad[1]))
    assert "echo" not in str(raised.value)
    assert source.health == "failing"
    assert {j.status for j in session.scalars(select(Job))} == {"new"}  # nothing was closed


def test_a_board_name_is_required(session: Session) -> None:
    source = propose_source(session, "greenhouse", "Acme", URL)
    approve_source(session, source.id)
    with pytest.raises(FetchError, match="board"):
        fetch_greenhouse(source, client(BOARD))


def test_html_to_text() -> None:
    assert html_to_text("&lt;div&gt;A &amp;amp; B&lt;br&gt;C&lt;/div&gt;") == "A & B\nC"


def test_hub_ingest_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    assert main(["ingest", "greenhouse", "--company", "acme"], store) == 1
    assert "hub sources add" in capsys.readouterr().out
    main(["sources", "add", "greenhouse", "Acme", URL], store)
    assert main(["ingest", "greenhouse", "--company", "acme"], store) == 1  # not approved yet
    assert "approve it first" in capsys.readouterr().out
    main(["sources", "approve", "1"], store)
    capsys.readouterr()
    from careerhub.cli import _with_session

    assert _with_session(store, lambda s: _ingest(s, "acme", client(BOARD))) == 0
    assert "2 new" in capsys.readouterr().out
