"""Tests for `hub add-job` (CH-96). No test makes a network call; LinkedIn is never requested."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import _add_job
from careerhub.core.migrate import migrate
from careerhub.core.models import Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, approve_source, propose_source, store_jobs
from careerhub.modules.jobs.manual import (
    ManualJobError,
    PostingRef,
    add_job,
    classify,
    guess_from_link,
    is_never_requested,
)

LINKEDIN = (
    "https://www.linkedin.com/jobs/view/machine-learning-engineer-at-acme-robotics-3912345678"
)
HANDSHAKE = "https://app.joinhandshake.com/stu/jobs/1234567"
GH_URL = "https://boards.greenhouse.io/acme/jobs/101"
LEVER_URL = "https://jobs.lever.co/acme/11111111-aaaa-bbbb-cccc-000000000001"
ASHBY_URL = "https://jobs.ashbyhq.com/acme/aaaaaaaa-0000-0000-0000-000000000001"


def client(routes: dict[str, Any], seen: list[httpx.Request]) -> httpx.Client:
    """Answer by URL path; any request to an unlisted address fails the test."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert not is_never_requested(str(request.url)), "requested a forbidden site"
        key = request.url.host + request.url.path
        if key not in routes:
            return httpx.Response(404, content="echo")
        return httpx.Response(200, content=json.dumps(routes[key]))

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (GH_URL, PostingRef("greenhouse", "acme", "101")),
        (
            "https://job-boards.greenhouse.io/acme/jobs/101?gh_src=x",
            PostingRef("greenhouse", "acme", "101"),
        ),
        (LEVER_URL, PostingRef("lever", "acme", "11111111-aaaa-bbbb-cccc-000000000001")),
        (ASHBY_URL, PostingRef("ashby", "acme", "aaaaaaaa-0000-0000-0000-000000000001")),
    ],
)
def test_ats_posting_addresses_are_recognized(url: str, expected: PostingRef) -> None:
    assert classify(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        LINKEDIN,
        HANDSHAKE,
        "https://example.com/job/1",
        "ftp://boards.greenhouse.io/a/jobs/1",
        "https://boards.greenhouse.io/../x/jobs/1",
        "https://jobs.lever.co/a b/c",
        "https://jobs.lever.co/only-one-part",
        "https://boards.greenhouse.io/acme/about/1",
        "",
        "not a url",
    ],
)
def test_other_addresses_are_not_posting_references(url: str) -> None:
    assert classify(url) is None


def test_linkedin_and_handshake_are_on_the_never_request_list() -> None:
    assert is_never_requested(LINKEDIN) and is_never_requested(HANDSHAKE)
    assert not is_never_requested(GH_URL) and not is_never_requested("https://notlinkedin.com/x")


def test_title_and_company_are_read_from_the_links_own_text_only() -> None:
    assert guess_from_link(LINKEDIN) == ("Machine Learning Engineer", "Acme Robotics")
    assert guess_from_link("https://example.com/careers") is None


def test_a_greenhouse_address_is_fetched_through_the_public_api(session: Session) -> None:
    seen: list[httpx.Request] = []
    record = {
        "id": 101,
        "title": "Engineer",
        "absolute_url": GH_URL,
        "content": "&lt;p&gt;Build&lt;/p&gt;",
        "location": {"name": "Boston, MA"},
    }
    http = client({"boards-api.greenhouse.io/v1/boards/acme/jobs/101": record}, seen)
    result = add_job(session, GH_URL, client=http)
    assert result.how == "fetched from greenhouse" and result.job.title == "Engineer"
    assert result.job.company.name == "Acme" and result.job.raw_text == "Build"
    assert [str(r.url) for r in seen] == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/101"
    ]
    assert session.get(Source, result.job.source_id).kind == "manual"  # type: ignore[union-attr]


def test_a_lever_address_is_fetched_with_the_company_the_user_gives(session: Session) -> None:
    posting = {
        "id": "11111111-aaaa-bbbb-cccc-000000000001",
        "text": "Analyst",
        "hostedUrl": LEVER_URL,
        "categories": {"location": "Remote"},
        "descriptionPlain": "Analyze.",
    }
    seen: list[httpx.Request] = []
    http = client(
        {"api.lever.co/v0/postings/acme/11111111-aaaa-bbbb-cccc-000000000001": posting}, seen
    )
    result = add_job(session, LEVER_URL, company="Acme Robotics", client=http)
    assert result.job.company.name == "Acme Robotics" and result.job.location == "Remote"


def test_an_ashby_address_is_found_in_the_boards_listing_or_reported_missing(
    session: Session,
) -> None:
    board = {
        "jobs": [
            {
                "id": "aaaaaaaa-0000-0000-0000-000000000001",
                "title": "Dev",
                "jobUrl": ASHBY_URL,
                "descriptionPlain": "Code.",
            }
        ]
    }
    http = client({"api.ashbyhq.com/posting-api/job-board/acme": board}, [])
    assert add_job(session, ASHBY_URL, client=http).job.title == "Dev"
    gone = client({"api.ashbyhq.com/posting-api/job-board/acme": {"jobs": []}}, [])
    with pytest.raises(ManualJobError, match="not found"):
        add_job(session, ASHBY_URL.replace("0001", "0009"), client=gone)


def test_a_failed_fetch_is_a_clear_error_with_no_page_content(session: Session) -> None:
    with pytest.raises(ManualJobError) as raised:
        add_job(session, GH_URL, client=client({}, []))
    assert "404" in str(raised.value) and "echo" not in str(raised.value)


@pytest.mark.parametrize("url", [LINKEDIN, HANDSHAKE])
def test_linkedin_and_handshake_pages_are_never_requested(session: Session, url: str) -> None:
    seen: list[httpx.Request] = []
    result = add_job(session, url, "Acme", "ML Engineer", "Pasted description.", client({}, seen))
    assert result.how == "stored from the text you pasted" and seen == []
    assert result.job.url == url and result.job.raw_text == "Pasted description."
    assert session.get(Source, result.job.source_id).kind == "manual"  # type: ignore[union-attr]


def test_a_job_already_in_the_list_is_found_instead_of_duplicated(session: Session) -> None:
    board = approve_source(
        session, propose_source(session, "greenhouse", "Acme", "u", {"board": "acme"}).id
    )
    store_jobs(
        session, board, [NormalizedJob("Acme", "g1", "ML Engineer", "https://g/1", "Boston")]
    )
    seen: list[httpx.Request] = []
    result = add_job(session, LINKEDIN, "ACME", "ml  engineer", None, client({}, seen))
    assert "already in your list" in result.how and seen == []
    assert len(session.scalars(select(Job)).all()) == 1


def test_the_companys_own_board_is_searched_when_a_source_for_it_is_approved(
    session: Session,
) -> None:
    board = approve_source(
        session,
        propose_source(
            session, "greenhouse", "Acme", "https://boards.greenhouse.io/acme", {"board": "acme"}
        ).id,
    )
    listing = {
        "jobs": [{"id": 5, "title": "ML Engineer", "absolute_url": "https://g/5", "content": ""}]
    }
    seen: list[httpx.Request] = []
    http = client({"boards-api.greenhouse.io/v1/boards/acme/jobs": listing}, seen)
    result = add_job(session, LINKEDIN, "Acme", "ML Engineer", None, http)
    assert result.job.source_id == board.id and "company's own board" in result.how
    assert all(r.url.host == "boards-api.greenhouse.io" for r in seen)


def test_missing_details_are_named_in_the_error(session: Session) -> None:
    with pytest.raises(ManualJobError, match="company, title, description text"):
        add_job(session, "https://example.com/careers/1")
    with pytest.raises(ManualJobError, match="description text"):
        add_job(session, "https://example.com/careers/1", "Acme", "Engineer")


def test_manual_jobs_are_never_closed_by_adding_another(session: Session) -> None:
    add_job(session, "https://example.com/1", "Acme", "Engineer", "Text one")
    add_job(session, "https://example.com/2", "Acme", "Analyst", "Text two")
    assert {j.status for j in session.scalars(select(Job))} == {"new"}


def test_adding_the_same_link_twice_makes_one_job(session: Session) -> None:
    add_job(session, "https://example.com/1", "Acme", "Engineer", "Text")
    again = add_job(session, "https://example.com/1", "Acme", "Engineer", "Text, edited")
    assert "already in your list" in again.how
    assert len(session.scalars(select(Job)).all()) == 1


def test_the_command_reads_the_description_from_stdin_or_a_file_and_asks_for_nothing_else(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    piped = io.StringIO("Description from stdin.")
    options = ["--company", "Acme", "--title", "ML Engineer"]
    assert _add_job(session, "https://example.com/j/1", options, client({}, []), stdin=piped) == 0
    assert "Added #1" in capsys.readouterr().out
    file = tmp_path / "d.txt"
    file.write_text("Description from a file.", encoding="utf-8")
    flags = ["--company", "Globex", "--title", "Analyst", "--text-file", str(file)]
    assert (
        _add_job(session, "https://example.com/j/2", flags, client({}, []), stdin=io.StringIO())
        == 0
    )
    texts = {j.title: j.raw_text for j in session.scalars(select(Job))}
    assert texts == {
        "ML Engineer": "Description from stdin.",
        "Analyst": "Description from a file.",
    }


def test_the_command_uses_the_links_own_text_for_company_and_title(session: Session) -> None:
    stdin = io.StringIO("Pasted.")
    assert _add_job(session, LINKEDIN, [], client({}, []), stdin=stdin) == 0
    job = session.scalars(select(Job)).one()
    assert (job.title, job.company.name) == ("Machine Learning Engineer", "Acme Robotics")


def test_the_command_reports_what_is_missing_and_bad_options(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add_job(session, "https://example.com/j", [], None, stdin=io.StringIO()) == 1
    assert "company" in capsys.readouterr().out
    assert (
        _add_job(session, "https://example.com/j", ["--bogus", "x"], None, stdin=io.StringIO()) == 2
    )
    missing = ["--company", "A", "--title", "T", "--text-file", "/nonexistent/file.txt"]
    assert _add_job(session, "https://example.com/j", missing, None, stdin=io.StringIO()) == 1
