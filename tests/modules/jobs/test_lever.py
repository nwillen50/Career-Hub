"""Tests for the Lever adapter (CH-93). The sample is made up in the shape of Lever's public API."""

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
from careerhub.core.models import Job
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import (
    FetchError,
    SourceNotApproved,
    approve_source,
    fetch_lever,
    ingest_lever,
    propose_source,
)
from careerhub.modules.jobs.lever import description, normalize

POSTINGS: list[dict[str, Any]] = [
    {
        "id": "11111111-aaaa-bbbb-cccc-000000000001",
        "text": "Software Engineer, New Grad",
        "hostedUrl": "https://jobs.lever.co/acme/11111111-aaaa-bbbb-cccc-000000000001",
        "applyUrl": "https://jobs.lever.co/acme/11111111-aaaa-bbbb-cccc-000000000001/apply",
        "categories": {"location": "Boston, MA", "team": "Engineering", "commitment": "Full-time"},
        "descriptionPlain": "Build things with a small team.",
        "lists": [{"text": "Requirements", "content": "<li>Python</li><li>SQL</li>"}],
        "additionalPlain": "We are an equal opportunity employer.",
        "createdAt": 1760000000000,
    },
    {
        "id": "11111111-aaaa-bbbb-cccc-000000000002",
        "text": "Data Analyst",
        "hostedUrl": "https://jobs.lever.co/acme/11111111-aaaa-bbbb-cccc-000000000002",
        "categories": {},
    },
]
URL = "https://jobs.lever.co/acme"


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


def approved(session: Session, **config: str):  # type: ignore[no-untyped-def]
    source = propose_source(session, "lever", "Acme", URL, {"board": "acme", **config})
    return approve_source(session, source.id)


def test_postings_are_normalized_with_the_description_lists_and_closing_text() -> None:
    job = normalize(POSTINGS[0], "Acme")
    assert (job.company_name, job.title, job.location) == (
        "Acme",
        "Software Engineer, New Grad",
        "Boston, MA",
    )
    assert job.external_id.endswith("0001") and job.url.startswith("https://jobs.lever.co/acme/")
    assert job.raw_text == (
        "Build things with a small team.\nRequirements\nPython\nSQL\n"
        "We are an equal opportunity employer."
    )
    bare = normalize(POSTINGS[1], "Acme")
    assert bare.location is None and bare.raw_text is None


def test_a_hostile_description_shape_does_not_crash() -> None:
    assert (
        description({"lists": ["not a dict", {"text": None, "content": 5}], "descriptionPlain": 3})
        == ""
    )


def test_an_approved_source_is_fetched_from_the_public_api(session: Session) -> None:
    seen: list[httpx.Request] = []
    jobs = fetch_lever(approved(session), client(POSTINGS, seen=seen))
    assert [j.title for j in jobs] == ["Software Engineer, New Grad", "Data Analyst"]
    assert str(seen[0].url) == "https://api.lever.co/v0/postings/acme?mode=json"
    assert "authorization" not in seen[0].headers  # public data, no key


def test_the_eu_region_uses_the_eu_host(session: Session) -> None:
    seen: list[httpx.Request] = []
    source = approved(session, region="eu")
    fetch_lever(source, client([], seen=seen))
    assert seen[0].url.host == "api.eu.lever.co"
    source.config = {"board": "acme", "region": "mars"}
    with pytest.raises(FetchError, match="region"):
        fetch_lever(source, client([]))


def test_an_unapproved_source_is_never_fetched(session: Session) -> None:
    source = propose_source(session, "lever", "Acme", URL, {"board": "acme"})
    seen: list[httpx.Request] = []
    with pytest.raises(SourceNotApproved):
        fetch_lever(source, client(POSTINGS, seen=seen))
    assert seen == []


@pytest.mark.parametrize("board", ["", "a/b", "../x", "a b"])
def test_bad_board_names_are_rejected_before_any_request(session: Session, board: str) -> None:
    source = approve_source(
        session, propose_source(session, "lever", "A", URL, {"board": board}).id
    )
    seen: list[httpx.Request] = []
    with pytest.raises(FetchError):
        fetch_lever(source, client(POSTINGS, seen=seen))
    assert seen == []


@pytest.mark.parametrize(
    "bad",
    [httpx.ConnectError("boom"), {"jobs": []}, "text", [{"id": "x"}]],
)
def test_failures_and_odd_shapes_are_fetch_errors_with_no_content(
    session: Session, bad: Any
) -> None:
    with pytest.raises(FetchError) as raised:
        fetch_lever(approved(session), client(bad))
    assert "boom" not in str(raised.value)


def test_a_bad_status_never_closes_jobs_and_marks_the_source_failing(session: Session) -> None:
    source = approved(session)
    ingest_lever(session, source, client(POSTINGS))
    with pytest.raises(FetchError) as raised:
        ingest_lever(session, source, client(POSTINGS, status=500))
    assert "echo" not in str(raised.value) and source.health == "failing"
    assert {j.status for j in session.scalars(select(Job))} == {"new"}


def test_ingest_stores_jobs_and_closes_the_ones_that_disappear(session: Session) -> None:
    source = approved(session)
    first = ingest_lever(session, source, client(POSTINGS))
    assert first.new == 2 and source.health == "ok"
    second = ingest_lever(session, source, client(POSTINGS[:1]))
    assert second.closed == 1


def test_an_empty_listing_for_a_board_that_had_jobs_is_kept_open_and_failing(
    session: Session,
) -> None:
    source = approved(session)
    ingest_lever(session, source, client(POSTINGS))
    with pytest.raises(FetchError, match="Lever listed no jobs"):
        ingest_lever(session, source, client([]))
    assert source.health == "failing"
    assert {j.status for j in session.scalars(select(Job))} == {"new"}


def test_hub_ingest_lever_and_sources_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    assert main(["ingest", "lever", "--company", "acme"], store) == 1
    assert "No lever source" in capsys.readouterr().out
    main(["sources", "add", "lever", "Acme", URL], store)
    main(["sources", "approve", "1"], store)
    capsys.readouterr()
    from careerhub.cli import _with_session

    assert _with_session(store, lambda s: _ingest(s, "acme", client(POSTINGS), "lever")) == 0
    assert "2 new" in capsys.readouterr().out
