"""The "Group by place" toggle on Today and the Tracker (CH-170). Saved sample data only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from a11y import check_page
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_today import add_job

from careerhub.core.migrate import migrate
from careerhub.core.models import Job
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.applications import track
from careerhub.web.app import create_app
from careerhub.web.tracker_view import TrackerQuery, clean_query

TOKEN = "t"
PORT = 8765
PLACES = ("Boston, MA", "London, UK", "Remote", "Cambridge, MA", None)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    db = session_factory(engine)()
    for n, place in enumerate(PLACES, 1):
        job = add_job(db, n, 90 - n, company=f"Firm{n}")
        job.location = place
        track(db, job.id, "applied")
    db.commit()
    return db


def web(session: Session) -> TestClient:
    app = create_app(TOKEN, PORT, lambda: True, session_factory(session.get_bind()))
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False)
    c.get(f"/?t={TOKEN}")
    return c


def test_today_is_a_plain_ranked_list_until_asked(session: Session) -> None:
    html = web(session).get("/today").text
    assert "Group by place" in html and "United States (" not in html
    assert check_page(html) == []


def test_today_groups_cards_under_country_and_city_headings(session: Session) -> None:
    html = web(session).get("/today?group=place").text
    assert "<h3>United States (2)</h3>" in html and "<h3>United Kingdom (1)</h3>" in html
    assert "<h4>Boston (1)</h4>" in html and "<h4>London (1)</h4>" in html
    assert html.index("United States") < html.index("United Kingdom") < html.index("Remote (1)")
    assert html.index("Remote (1)") < html.index("Place not stated (1)")
    assert "Show best first" in html and "group=place" in html
    assert check_page(html) == []  # heading levels never skip


def test_every_job_appears_once_with_its_overall_rank(session: Session) -> None:
    html = web(session).get("/today?group=place").text
    assert html.count('class="card job"') == len(PLACES)
    assert "<h5>4. " in html  # Cambridge keeps its overall rank under its own city heading


def test_the_section_chips_keep_the_grouping(session: Session) -> None:
    html = web(session).get("/today?group=place").text
    assert "/today?section=top&amp;group=place" in html


def test_an_unknown_group_value_means_the_normal_list(session: Session) -> None:
    html = web(session).get("/today?group=zzz").text
    assert "United States (" not in html and "Group by place" in html


def test_the_tracker_groups_rows_into_bodies_with_headings(session: Session) -> None:
    html = web(session).get("/tracker?group=place").text
    assert html.count("<tbody>") == 4  # United States, United Kingdom, Remote, not stated
    assert 'scope="rowgroup" colspan="7">United States (2)' in html
    assert "Cambridge (1)" in html and "Boston (1)" in html
    assert html.count("<tr id=") == len(PLACES)
    assert "Show as one list" in html and check_page(html) == []


def test_the_tracker_keeps_its_sort_inside_each_group_and_the_toggle_in_links(
    session: Session,
) -> None:
    html = web(session).get("/tracker?group=place&sort=company&dir=desc").text
    assert html.index("Firm4") < html.index(
        "Firm1"
    )  # descending: Cambridge's job leads its country
    assert "group=place" in html and "/tracker.csv" in html


def test_the_query_only_accepts_the_one_value() -> None:
    assert clean_query(None, None, [], None, None, "place").group is True
    assert clean_query(None, None, [], None, None, "zzz").group is False
    assert "group=place" in TrackerQuery(group=True).params()
    assert "group" not in TrackerQuery().params()


def test_grouping_ignores_jobs_with_no_location_and_never_loses_them(session: Session) -> None:
    assert session.query(Job).filter(Job.location.is_(None)).count() == 1
    assert "Place not stated (1)" in web(session).get("/tracker?group=place").text


def test_the_tracker_toggle_link_really_flips_the_grouping(session: Session) -> None:
    """Review: the link used to carry the current setting, so clicking it changed nothing."""
    c = web(session)
    plain = c.get("/tracker").text
    on = re.search(r'<a href="([^"]+)">Group by place</a>', plain)
    assert on and "group=place" in on.group(1).replace("&amp;", "&")
    grouped = c.get(on.group(1).replace("&amp;", "&")).text
    off = re.search(r'<a href="([^"]+)">Show as one list</a>', grouped)
    assert off and "group=place" not in off.group(1)
    assert "Show as one list" not in c.get(off.group(1).replace("&amp;", "&")).text
