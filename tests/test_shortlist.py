from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Job, Source
from careerhub.core.runlog import finish_run, start_run
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.web.shortlist import (
    ShortlistData,
    ShortlistEntry,
    gather_shortlist,
    render_shortlist,
    write_shortlist,
)

NOW = datetime(2026, 10, 21, 7, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def entry(**kw: object) -> ShortlistEntry:
    base: dict[str, object] = {
        "title": "ML Engineer",
        "company": "Acme",
        "location": "Boston, MA",
        "url": "https://example.com/jobs/1",
        "score": 82.0,
        "explanation": "Strong match on field. Level fits too.",
        "criteria": [{"name": "field", "score": 9, "reason": "Right field"}],
    }
    base.update(kw)
    return ShortlistEntry(**base)  # type: ignore[arg-type]


def test_a_job_shows_title_link_score_reason_and_criteria() -> None:
    html = render_shortlist(ShortlistData([entry()], NOW), NOW)
    assert 'href="https://example.com/jobs/1"' in html and 'rel="noopener noreferrer"' in html
    assert "82/100" in html and "Strong match on field." in html
    assert "<summary>Full explanation</summary>" in html and "Right field" in html
    assert html.startswith("<!doctype html>") and 'lang="en"' in html and "<h1>" in html


def test_hostile_posting_text_is_escaped_and_scripts_never_run() -> None:
    evil = entry(
        title="<script>alert(1)</script>",
        company='"><img src=x onerror=alert(2)>',
        explanation="<b>bold</b>",
        criteria=[{"name": "<i>", "score": 1, "reason": "<script>"}],
    )
    html = render_shortlist(ShortlistData([evil], NOW), NOW)
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "//x"]
)
def test_only_http_and_https_links_are_clickable(url: str) -> None:
    html = render_shortlist(ShortlistData([entry(url=url)], NOW), NOW)
    assert "href=" not in html and "ML Engineer" in html


def test_quotes_in_a_url_cannot_break_out_of_the_attribute() -> None:
    html = render_shortlist(ShortlistData([entry(url='https://x.com/a" onclick="evil')], NOW), NOW)
    assert 'onclick="evil' not in html and "&quot;" in html


def test_empty_state_and_error_state() -> None:
    empty = render_shortlist(ShortlistData(), NOW)
    assert "Nothing to show yet" in empty and "No successful run yet." in empty
    failed = render_shortlist(ShortlistData([entry()], NOW, "SourceFetchError"), NOW)
    assert 'role="alert"' in failed and "SourceFetchError" in failed and "earlier run" in failed


def test_the_page_has_no_scripts_or_external_requests() -> None:
    html = render_shortlist(ShortlistData([entry()], NOW), NOW)
    assert "<script" not in html and "src=" not in html and "<link" not in html


def test_dark_mode_and_viewport_are_supported() -> None:
    html = render_shortlist(ShortlistData(), NOW)
    assert "prefers-color-scheme: dark" in html and 'name="viewport"' in html


def seed(session: Session, count: int) -> None:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(
        session,
        source,
        [
            NormalizedJob("Acme", str(i), f"Job {i}", "https://example.com/" + str(i), "Boston, MA")
            for i in range(count)
        ],
    )
    for job in session.scalars(select(Job)):
        session.add(
            Assessment(
                job_id=job.id,
                filter_passed=True,
                fit_score=float(job.external_id) * 10,
                explanation=f"Why {job.title}.",
                criteria_scores=[],
            )
        )
    session.commit()


def test_gather_takes_the_top_five_best_first(session: Session) -> None:
    seed(session, 8)
    data = gather_shortlist(session)
    assert [e.title for e in data.entries] == ["Job 7", "Job 6", "Job 5", "Job 4", "Job 3"]
    assert data.entries[0].explanation == "Why Job 7."


def test_gather_reports_the_last_success_and_a_newer_failure(session: Session) -> None:
    seed(session, 1)
    ok = finish_run(session, start_run(session, "nightly"))
    assert gather_shortlist(session).last_success == ok.finished_at
    assert gather_shortlist(session).last_error is None
    finish_run(session, start_run(session, "nightly"), error=ValueError("secret detail"))
    data = gather_shortlist(session)
    assert data.last_error == "ValueError" and data.last_success == ok.finished_at
    assert "secret detail" not in render_shortlist(data, NOW)


def test_other_steps_runs_are_ignored(session: Session) -> None:
    finish_run(session, start_run(session, "backup"), error=ValueError("x"))
    assert gather_shortlist(session).last_error is None


def test_the_page_is_written_privately_and_replaced_whole(session: Session, tmp_path: Path) -> None:
    seed(session, 2)
    path = write_shortlist(session, NOW, tmp_path / "out" / "index.html")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "Job 1" in path.read_text(encoding="utf-8")
    write_shortlist(session, NOW + timedelta(days=1), path)
    assert not list(path.parent.glob("*.tmp"))


def test_hub_shortlist_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from careerhub.cli import main
    from careerhub.core.secrets import InMemoryKeyStore

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert main(["shortlist", "build"], InMemoryKeyStore()) == 0
    page = tmp_path / "shortlist" / "index.html"
    assert str(page) in capsys.readouterr().out and "Nothing to show yet" in page.read_text()


def test_who_to_contact_is_shown_escaped_and_limited_to_two() -> None:
    from careerhub.modules.network import WarmPath

    contacts = [
        WarmPath(1, "Ada <b>Sample</b>", "Engineer & Lead", 4, ["you noted: <i>mentor</i>"]),
        WarmPath(2, "Ben Example", None, 1, ["LinkedIn connection"]),
        WarmPath(3, "Cy Extra", None, 0, []),
    ]
    html = render_shortlist(ShortlistData([entry(contacts=contacts[:2])], NOW), NOW)
    assert "Who to contact" in html and "Ben Example" in html and "LinkedIn connection" in html
    assert "<b>Sample</b>" not in html and "&lt;b&gt;Sample&lt;/b&gt;" in html
    assert "Engineer &amp; Lead" in html and "<i>mentor</i>" not in html


def test_a_job_with_no_known_contacts_says_so() -> None:
    html = render_shortlist(ShortlistData([entry()], NOW), NOW)
    assert "no one you know here yet" in html


def test_gather_attaches_the_two_strongest_contacts(session: Session) -> None:
    from careerhub.core.models import Person

    seed(session, 1)
    job = session.scalars(select(Job)).one()
    for name, how in (("Ann", "friend"), ("Bea", None), ("Cat", "mentor; alumni: Club")):
        session.add(Person(full_name=name, company_id=job.company_id, how_known=how))
    session.commit()
    contacts = gather_shortlist(session).entries[0].contacts
    assert [c.name for c in contacts] == ["Cat", "Ann"]
