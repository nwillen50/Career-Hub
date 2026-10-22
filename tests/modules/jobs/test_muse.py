"""Tests for The Muse source and partial listings (CH-95). The sample is made up."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import (
    FetchError,
    NormalizedJob,
    SourceNotApproved,
    approve_source,
    fetch_muse,
    ingest_muse,
    propose_source,
    store_jobs,
)
from careerhub.modules.jobs.muse import normalize
from careerhub.modules.jobs.store import PARTIAL_CLOSE_DAYS


def record(job_id: int, name: str = "Software Engineer", company: str = "Acme") -> dict[str, Any]:
    return {
        "id": job_id,
        "name": name,
        "contents": "<p>Build things.</p><ul><li>Python</li></ul>",
        "locations": [{"name": "Boston, MA"}, {"name": "Flexible / Remote"}],
        "levels": [{"name": "Entry Level", "short_name": "entry"}],
        "company": {"id": 9, "name": company},
        "refs": {"landing_page": f"https://www.themuse.com/jobs/acme/{job_id}"},
    }


def page(*jobs: dict[str, Any], page_count: int = 1) -> dict[str, Any]:
    return {"page": 0, "page_count": page_count, "results": list(jobs)}


def client(
    pages: list[Any],
    seen: list[httpx.Request] | None = None,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(pages[0], Exception):
            raise pages[0]
        index = min(int(request.url.params.get("page", 0)), len(pages) - 1)
        return httpx.Response(status, content=json.dumps(pages[index]), headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def approved(session: Session, **config: str) -> Source:
    source = propose_source(session, "themuse", "The Muse", "https://www.themuse.com", dict(config))
    source = approve_source(session, source.id)
    source.config = dict(config)  # the same address returns the same source; set this test's config
    return source


def test_a_muse_job_keeps_its_muse_address_and_location_and_text() -> None:
    job = normalize(record(7))
    assert job.url == "https://www.themuse.com/jobs/acme/7" and job.company_name == "Acme"
    assert job.location == "Boston, MA; Flexible / Remote"
    assert job.raw_text == "Build things.\nPython"


def test_the_search_uses_the_configured_filters_and_reads_each_page(session: Session) -> None:
    seen: list[httpx.Request] = []
    source = approved(session, category="Software Engineering", level="Entry Level", pages="2")
    jobs = fetch_muse(
        source, client([page(record(1), page_count=2), page(record(2), page_count=2)], seen)
    )
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert [dict(r.url.params) for r in seen] == [
        {"page": "0", "category": "Software Engineering", "level": "Entry Level"},
        {"page": "1", "category": "Software Engineering", "level": "Entry Level"},
    ]
    assert all("api_key" not in r.url.params and "authorization" not in r.headers for r in seen)


def test_reading_stops_at_the_last_page_and_at_the_page_cap(session: Session) -> None:
    seen: list[httpx.Request] = []
    fetch_muse(approved(session), client([page(record(1), page_count=1)], seen))
    assert len(seen) == 1
    seen.clear()
    fetch_muse(approved(session, pages="99"), client([page(record(1), page_count=50)], seen))
    assert len(seen) == 5  # never more than MAX_PAGES


def test_the_rate_limit_header_stops_the_search_but_keeps_what_was_found(session: Session) -> None:
    seen: list[httpx.Request] = []
    out = fetch_muse(
        approved(session),
        client([page(record(1), page_count=9)], seen, headers={"X-RateLimit-Remaining": "0"}),
    )
    assert len(out) == 1 and len(seen) == 1


def test_unapproved_sources_are_never_searched_and_bad_pages_are_errors(session: Session) -> None:
    source = propose_source(session, "themuse", "The Muse", "https://www.themuse.com", {})
    seen: list[httpx.Request] = []
    with pytest.raises(SourceNotApproved):
        fetch_muse(source, client([page()], seen))
    assert seen == []
    with pytest.raises(FetchError, match="not a number"):
        fetch_muse(approved(session, pages="lots"), client([page()]))


@pytest.mark.parametrize(
    "bad", [httpx.ConnectError("boom"), {"results": [{"id": 1}]}, [], {"results": "x"}]
)
def test_failures_and_odd_shapes_are_fetch_errors(session: Session, bad: Any) -> None:
    with pytest.raises(FetchError) as raised:
        fetch_muse(approved(session), client([bad]))
    assert "boom" not in str(raised.value)


def test_a_job_missing_from_a_search_is_not_closed_but_an_old_one_is(session: Session) -> None:
    source = approved(session)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    items = [NormalizedJob("Acme", str(i), f"Role {i}", f"https://x/{i}", "Boston") for i in (1, 2)]
    store_jobs(session, source, items, now=start, partial=True)
    # Night 2 finds only job 1; job 2 was last seen one day ago, so it stays open.
    store_jobs(session, source, items[:1], now=start + timedelta(days=1), partial=True)
    assert {j.external_id: j.status for j in session.scalars(select(Job))} == {
        "1": "new",
        "2": "new",
    }
    # Day 14 or later without seeing job 2 closes it; job 1 was seen the day before.
    store_jobs(
        session, source, items[:1], now=start + timedelta(days=PARTIAL_CLOSE_DAYS), partial=True
    )
    assert {j.external_id: j.status for j in session.scalars(select(Job))} == {
        "1": "new",
        "2": "closed",
    }
    # If it shows up again it reopens.
    store_jobs(session, source, items, now=start + timedelta(days=15), partial=True)
    assert {j.status for j in session.scalars(select(Job))} == {"new"}


def test_a_complete_listing_still_closes_a_missing_job_at_once(session: Session) -> None:
    source = approved(session)
    items = [NormalizedJob("Acme", str(i), f"Role {i}", f"https://x/{i}", None) for i in (1, 2)]
    store_jobs(session, source, items)
    assert store_jobs(session, source, items[:1]).closed == 1


def test_an_empty_search_is_normal_not_a_glitch_and_the_source_stays_healthy(
    session: Session,
) -> None:
    source = approved(session)
    ingest_muse(session, source, client([page(record(1))]))
    report = ingest_muse(session, source, client([page()]))
    assert report.closed == 0 and source.health == "ok"
    assert [j.status for j in session.scalars(select(Job))] == ["new"]


def test_a_muse_job_already_on_a_company_board_is_a_duplicate(session: Session) -> None:
    board = propose_source(session, "greenhouse", "Acme", "https://boards.greenhouse.io/acme", {})
    approve_source(session, board.id)
    store_jobs(
        session, board, [NormalizedJob("Acme", "g1", "Software Engineer", "u", "Boston, MA")]
    )
    report = ingest_muse(
        session,
        approved(session),
        client([{**page(), "results": [{**record(1), "locations": [{"name": "Boston, MA"}]}]}]),
    )
    assert report.new == 1 and report.duplicates == 1


def test_hub_sources_add_takes_muse_search_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from careerhub.cli import main
    from careerhub.core.secrets import InMemoryKeyStore
    from careerhub.core.storage import open_database

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    url = "https://www.themuse.com"
    assert main(["sources", "add", "themuse", "Muse", url, "--level", "Entry Level"], store) == 0
    assert main(["sources", "add", "greenhouse", "A", url, "--level", "x"], store) == 2
    assert "not valid for greenhouse" in capsys.readouterr().out
    with session_factory(open_database(store))() as session:
        assert session.scalars(select(Source)).one().config == {"level": "Entry Level"}


def test_a_job_repeated_across_pages_is_stored_once_and_does_not_crash(session: Session) -> None:
    pages = [page(record(1), record(2), page_count=2), page(record(2), record(3), page_count=2)]
    source = approved(session, pages="2")
    report = ingest_muse(session, source, client(pages))
    assert report.new == 3 and len(session.scalars(select(Job)).all()) == 3


def test_store_jobs_ignores_a_repeated_id_in_one_listing(session: Session) -> None:
    source = approved(session)
    item = NormalizedJob("Acme", "1", "Role", "u", None)
    assert store_jobs(session, source, [item, item]).new == 1
