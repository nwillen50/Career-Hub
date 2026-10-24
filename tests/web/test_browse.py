"""The Browse tab, part 1 (CH-187): every open job, search, sort, paging, the same cards."""

from __future__ import annotations

from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Job
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.web.app import create_app
from careerhub.web.browse_view import PAGE_SIZE, BrowseQuery, gather_browse

TOKEN = "t"
PORT = 8765


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    client.get(f"/?t={TOKEN}")
    return client


def titles(session: Session, **query: object) -> list[str]:
    view = gather_browse(session, BrowseQuery(**query))  # type: ignore[arg-type]
    return [card.entry.title for card in view.cards]


def test_it_lists_every_open_job_not_just_five_best_first(session: Session) -> None:
    for n in range(1, 9):
        add_job(session, n, 90 - n)
    html = web(session).get("/browse").text
    assert html.count('class="card job"') == 8 and "8 open jobs" in html
    assert html.index("Role 1") < html.index("Role 2") < html.index("Role 8")
    assert check_page(html) == []


def test_closed_filtered_out_and_duplicate_jobs_are_left_out(session: Session) -> None:
    keep = add_job(session, 1, 80)
    closed, filtered, dupe = (
        add_job(session, 2, 70),
        add_job(session, 3, 60),
        add_job(session, 4, 50),
    )
    closed.status = "closed"
    dupe.duplicate_of_id = keep.id
    session.query(Assessment).filter_by(job_id=filtered.id).update({"filter_passed": False})
    session.commit()
    assert titles(session) == ["Role 1"]


def test_search_looks_in_title_company_place_and_summary_and_every_word_must_match(
    session: Session,
) -> None:
    add_job(session, 1, 80, company="Acme Robotics", title="Robotics Engineer")
    add_job(session, 2, 70, company="Globex", title="Analyst")
    third = add_job(session, 3, 60, company="Initech", title="Developer")
    third.location = "Austin, TX"
    third.structured_fields = {"fields": {"summary": "Builds satellites for farms."}}
    session.commit()
    assert titles(session, text="robotics") == ["Robotics Engineer"]
    assert titles(session, text="GLOBEX") == ["Analyst"]
    assert titles(session, text="austin") == ["Developer"]
    assert titles(session, text="satellites") == ["Developer"]
    assert titles(session, text="acme engineer") == ["Robotics Engineer"]
    assert titles(session, text="acme analyst") == []


def test_search_text_is_matched_literally_not_as_a_pattern_or_sql(session: Session) -> None:
    add_job(session, 1, 80, title="100% Remote Engineer")
    add_job(session, 2, 70, title="Other")
    assert titles(session, text="%") == ["100% Remote Engineer"]
    assert titles(session, text="_") == []
    assert titles(session, text="'; DROP TABLE job; --") == []
    assert session.query(Job).count() == 2


def test_sorting_by_newest_company_and_deadline(session: Session) -> None:
    from datetime import timedelta

    a = add_job(session, 1, 90, company="Zed", deadline="2026-12-01")
    b = add_job(session, 2, 80, company="Alpha", deadline="2026-11-01")
    c = add_job(session, 3, 70, company="Mid")
    a.first_seen_at = b.first_seen_at - timedelta(days=5)
    c.first_seen_at = b.first_seen_at + timedelta(days=5)
    session.commit()
    assert titles(session, sort="company") == ["Role 2", "Role 3", "Role 1"]
    assert titles(session, sort="newest") == ["Role 3", "Role 2", "Role 1"]
    assert titles(session, sort="deadline") == ["Role 2", "Role 1", "Role 3"]  # none last


def test_pages_hold_25_and_the_url_keeps_the_view(session: Session) -> None:
    for n in range(1, 31):
        add_job(session, n, 100 - n)
    client = web(session)
    first = client.get("/browse").text
    assert first.count('class="card job"') == PAGE_SIZE and "Page 1 of 2" in first
    assert 'href="/browse?page=2"' in first and "Previous page" not in first
    second = client.get("/browse?page=2").text
    assert second.count('class="card job"') == 5 and "Previous page" in second
    assert "Role 26" in second and "Role 1<" not in second
    far = client.get("/browse?page=99").text  # past the end: the last page, not an error
    assert "Page 2 of 2" in far
    assert client.get("/browse?page=abc&sort=bogus&q=" + "x" * 500).status_code == 200


def test_the_query_survives_in_links_and_group_by_place_works(session: Session) -> None:
    for n in range(1, 4):
        add_job(session, n, 90 - n)
    html = web(session).get("/browse?q=role&sort=company&group=place").text
    assert "Boston" in html and 'value="role"' in html
    assert BrowseQuery("role", "company", 2, True).params(page=3) == (
        "q=role&sort=company&page=3&group=place"
    )
    assert check_page(html) == []


def test_hostile_text_is_escaped_and_the_empty_state_is_designed(session: Session) -> None:
    client = web(session)
    assert "Nothing ranked yet" in client.get("/browse").text
    add_job(session, 1, 80, title="<script>alert(1)</script>")
    html = client.get('/browse?q=<script>"').text
    assert "<script>alert" not in html and "No jobs match" in html
    assert check_page(html) == []


def test_reactions_on_browse_use_the_same_buttons_as_today(session: Session) -> None:
    job = add_job(session, 1, 80)
    html = web(session).get("/browse").text
    assert f'action="/jobs/{job.id}/react"' in html and "Why it scored" in html


def test_review_findings_a_superscript_digit_in_the_page_is_not_a_500(session: Session) -> None:
    add_job(session, 1, 80)
    client = web(session)
    for odd in ("%C2%B2", "%EF%BC%92", "%D9%A2"):  # ², full-width 2, Arabic-indic 2
        assert client.get(f"/browse?page={odd}").status_code == 200
