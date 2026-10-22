"""Tests for the Ashby adapter (CH-94). The sample is made up in the shape of Ashby's public API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Job
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import (
    FetchError,
    SourceNotApproved,
    approve_source,
    fetch_ashby,
    ingest_ashby,
    propose_source,
)
from careerhub.modules.jobs.ashby import compensation_line, normalize

BOARD: dict[str, Any] = {
    "jobs": [
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000001",
            "title": "Software Engineer, New Grad",
            "location": "Boston, MA",
            "isRemote": False,
            "isListed": True,
            "descriptionPlain": "Build things.\nPython and SQL.",
            "jobUrl": "https://jobs.ashbyhq.com/acme/aaaaaaaa-0000-0000-0000-000000000001",
            "compensation": {
                "summaryComponents": [
                    {
                        "compensationType": "Salary",
                        "interval": "1 YEAR",
                        "currencyCode": "USD",
                        "minValue": 120000,
                        "maxValue": 150000,
                    },
                    {"compensationType": "EquityPercentage", "minValue": 0.1, "maxValue": 0.2},
                ]
            },
        },
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000002",
            "title": "Data Analyst",
            "location": "Remote",
            "isRemote": True,
            "descriptionHtml": "<p>Analyze</p><ul><li>SQL</li></ul>",
            "jobUrl": "https://jobs.ashbyhq.com/acme/aaaaaaaa-0000-0000-0000-000000000002",
        },
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000003",
            "title": "Hidden role",
            "isListed": False,
            "jobUrl": "https://jobs.ashbyhq.com/acme/aaaaaaaa-0000-0000-0000-000000000003",
        },
    ]
}
URL = "https://jobs.ashbyhq.com/acme"
BAD_SALARY = {"compensationType": "Salary", "minValue": "a", "maxValue": 1}


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


def approved(session: Session):  # type: ignore[no-untyped-def]
    source = propose_source(session, "ashby", "Acme", URL, {"board": "acme"})
    return approve_source(session, source.id)


def test_a_stated_salary_is_added_to_the_text_for_extraction_and_the_filter() -> None:
    job = normalize(BOARD["jobs"][0], "Acme")
    assert "Compensation: USD 120000 to 150000 per year" in (job.raw_text or "")
    assert "Equity" not in (job.raw_text or "")


def test_html_descriptions_remote_flag_and_no_salary() -> None:
    job = normalize(BOARD["jobs"][1], "Acme")
    assert job.raw_text == "Analyze\nSQL\nWorkplace: remote" and job.location == "Remote"


@pytest.mark.parametrize(
    "posting",
    [
        {},
        {"compensation": "x"},
        {"compensation": {"summaryComponents": ["x", {"compensationType": "Salary"}]}},
        {
            "compensation": {
                "summaryComponents": [
                    {"compensationType": "Salary", "minValue": "a", "maxValue": 1}
                ]
            }
        },
    ],
)
def test_odd_compensation_shapes_give_no_line(posting: dict[str, Any]) -> None:
    assert compensation_line(posting) == ""


def test_the_request_asks_for_pay_and_unlisted_jobs_are_left_out(session: Session) -> None:
    seen: list[httpx.Request] = []
    jobs = fetch_ashby(approved(session), client(BOARD, seen=seen))
    assert [j.title for j in jobs] == ["Software Engineer, New Grad", "Data Analyst"]
    assert str(seen[0].url) == (
        "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
    )


def test_an_unapproved_source_is_never_fetched(session: Session) -> None:
    source = propose_source(session, "ashby", "Acme", URL, {"board": "acme"})
    seen: list[httpx.Request] = []
    with pytest.raises(SourceNotApproved):
        fetch_ashby(source, client(BOARD, seen=seen))
    assert seen == []


@pytest.mark.parametrize(
    "bad", [httpx.ConnectError("boom"), [], "text", {"jobs": [{"id": "x"}]}, {"jobs": [1]}]
)
def test_failures_and_odd_shapes_are_fetch_errors(session: Session, bad: Any) -> None:
    with pytest.raises(FetchError) as raised:
        fetch_ashby(approved(session), client(bad))
    assert "boom" not in str(raised.value)


def test_a_bad_status_keeps_jobs_open_and_the_source_failing(session: Session) -> None:
    source = approved(session)
    ingest_ashby(session, source, client(BOARD))
    with pytest.raises(FetchError) as raised:
        ingest_ashby(session, source, client(BOARD, status=503))
    assert "echo" not in str(raised.value) and source.health == "failing"
    assert {j.status for j in session.scalars(select(Job))} == {"new"}


def test_ingest_stores_closes_and_treats_an_empty_listing_as_a_glitch(session: Session) -> None:
    source = approved(session)
    assert ingest_ashby(session, source, client(BOARD)).new == 2
    assert ingest_ashby(session, source, client({"jobs": BOARD["jobs"][:1]})).closed == 1
    with pytest.raises(FetchError, match="Ashby listed no jobs"):
        ingest_ashby(session, source, client({"jobs": []}))
    assert source.health == "failing"
    assert [j.status for j in session.scalars(select(Job).order_by(Job.id))] == ["new", "closed"]
