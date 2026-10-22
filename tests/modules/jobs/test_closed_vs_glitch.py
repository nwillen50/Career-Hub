"""Tests for telling a real "all jobs closed" from a glitch (CH-103). No network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import (
    FetchError,
    approve_source,
    ingest_ashby,
    ingest_greenhouse,
    ingest_lever,
    propose_source,
)

GH_BOARD = {
    "jobs": [
        {"id": i, "title": f"Role {i}", "absolute_url": f"https://g/{i}", "content": ""}
        for i in range(1, 6)
    ]
}
LEVER_POSTINGS = [
    {"id": f"p{i}", "text": f"Role {i}", "hostedUrl": f"https://l/{i}", "categories": {}}
    for i in range(1, 4)
]


class Board:
    """A fake ATS: a listing, plus per-job answers by job id (default: 404 not found)."""

    def __init__(self, listing: Any, per_job: dict[str, int] | None = None) -> None:
        self.listing = listing
        self.per_job = per_job or {}
        self.requests: list[str] = []

    def client(self) -> httpx.Client:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request.url.path)
            tail = request.url.path.rstrip("/").split("/")[-1]
            if request.url.path.endswith("/jobs") or request.url.path.endswith("acme"):
                return httpx.Response(200, content=json.dumps(self.listing))
            return httpx.Response(self.per_job.get(tail, 404), content="{}")

        return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def approved(session: Session, kind: str = "greenhouse", name: str = "Acme") -> Source:
    source = propose_source(session, kind, name, f"https://x/{kind}", {"board": "acme"})
    return approve_source(session, source.id)


def statuses(session: Session) -> set[str]:
    return {j.status for j in session.scalars(select(Job))}


def test_when_the_spot_checked_jobs_are_all_not_found_the_closures_are_real(
    session: Session,
) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(GH_BOARD).client())
    board = Board({"jobs": []})  # every per-job address answers 404
    report = ingest_greenhouse(session, source, board.client())
    assert statuses(session) == {"closed"} and report.closed == 5
    assert report.note == "Acme: all 5 jobs closed" and source.health == "ok"
    checked = [p for p in board.requests if "/jobs/" in p]
    assert len(checked) == 3  # at most three jobs are checked


def test_any_job_still_open_means_a_glitch_and_nothing_closes(session: Session) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(GH_BOARD).client())
    board = Board({"jobs": []}, per_job={"1": 404, "2": 200, "3": 404})
    with pytest.raises(FetchError, match="kept them"):
        ingest_greenhouse(session, source, board.client())
    assert statuses(session) == {"new"} and source.health == "failing"


@pytest.mark.parametrize("status", [500, 403, 429, 302])
def test_a_failed_check_is_a_glitch_too(session: Session, status: int) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(GH_BOARD).client())
    board = Board({"jobs": []}, per_job={str(i): status for i in range(1, 6)})
    with pytest.raises(FetchError):
        ingest_greenhouse(session, source, board.client())
    assert statuses(session) == {"new"}


def test_a_network_error_during_the_check_is_a_glitch(session: Session) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(GH_BOARD).client())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, content=json.dumps({"jobs": []}))
        raise httpx.ConnectError("boom")

    with pytest.raises(FetchError):
        ingest_greenhouse(session, source, httpx.Client(transport=httpx.MockTransport(handle)))
    assert statuses(session) == {"new"}


def test_lever_uses_its_own_per_job_address(session: Session) -> None:
    source = approved(session, "lever")
    ingest_lever(session, source, Board(LEVER_POSTINGS).client())
    board = Board([])
    report = ingest_lever(session, source, board.client())
    assert report.note == "Acme: all 3 jobs closed" and statuses(session) == {"closed"}
    assert "/v0/postings/acme/p1" in board.requests


def test_ashby_has_no_per_job_address_so_only_the_backstop_closes_after_three_nights(
    session: Session,
) -> None:
    source = approved(session, "ashby")
    listing = {"jobs": [{"id": "a1", "title": "Dev", "jobUrl": "https://a/1"}]}
    ingest_ashby(session, source, Board(listing).client())
    empty = Board({"jobs": []})
    for night in (1, 2):
        with pytest.raises(FetchError):
            ingest_ashby(session, source, empty.client())
        assert statuses(session) == {"new"} and source.health == "failing"
        assert source.config["empty_streak"] == str(night)
    report = ingest_ashby(session, source, empty.client())
    assert statuses(session) == {"closed"} and source.health == "ok"
    assert "3 nights in a row" in (report.note or "") and "empty_streak" not in source.config
    assert all("/jobs/" not in p for p in empty.requests)


def test_a_non_empty_night_resets_the_count(session: Session) -> None:
    source = approved(session, "ashby")
    listing = {"jobs": [{"id": "a1", "title": "Dev", "jobUrl": "https://a/1"}]}
    ingest_ashby(session, source, Board(listing).client())
    with pytest.raises(FetchError):
        ingest_ashby(session, source, Board({"jobs": []}).client())
    ingest_ashby(session, source, Board(listing).client())
    assert "empty_streak" not in source.config
    for _ in range(2):
        with pytest.raises(FetchError):
            ingest_ashby(session, source, Board({"jobs": []}).client())
    assert statuses(session) == {"new"}  # only two empty nights since the reset


def test_the_backstop_is_a_setting(session: Session, tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        Settings(empty_listing_backstop_nights=1).model_dump_json(), encoding="utf-8"
    )
    source = approved(session, "ashby")
    listing = {"jobs": [{"id": "a1", "title": "Dev", "jobUrl": "https://a/1"}]}
    ingest_ashby(session, source, Board(listing).client())
    ingest_ashby(session, source, Board({"jobs": []}).client())
    assert statuses(session) == {"closed"}


def test_a_board_that_lists_jobs_again_reopens_them(session: Session) -> None:
    source = approved(session)
    ingest_greenhouse(session, source, Board(GH_BOARD).client())
    ingest_greenhouse(session, source, Board({"jobs": []}).client())
    report = ingest_greenhouse(session, source, Board(GH_BOARD).client())
    assert report.reopened == 5 and statuses(session) == {"new"}


def test_a_board_with_no_open_jobs_that_lists_nothing_is_fine(session: Session) -> None:
    source = approved(session)
    report = ingest_greenhouse(session, source, Board({"jobs": []}).client())
    assert report.closed == 0 and source.health == "ok" and report.note is None


def test_the_setting_is_validated() -> None:
    for bad in (0, 31):
        with pytest.raises(ValueError):
            Settings(empty_listing_backstop_nights=bad)
