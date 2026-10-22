"""Tests for posting age, the gentle down-rank, and the footnote (CH-81). Data is made up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.jobs.age import age_days, posting_date
from careerhub.modules.jobs.scoring import ranked_jobs
from careerhub.web.shortlist import ShortlistData, gather_shortlist, render_shortlist

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)


def job(payload: dict[str, Any] | None = None, first_seen: datetime = NOW) -> Job:
    return Job(raw_payload=payload or {}, first_seen_at=first_seen, title="T")


@pytest.mark.parametrize(
    ("payload", "expected", "where"),
    [
        ({"first_published": "2026-10-01T10:00:00-04:00"}, "2026-10-01", "source"),
        ({"publishedAt": "2026-09-30T12:00:00Z"}, "2026-09-30", "source"),
        ({"createdAt": 1760000000000}, "2025-10-09", "source"),  # Lever: milliseconds
        ({"publication_date": "2026-10-10T00:00:00Z"}, "2026-10-10", "source"),
        ({"updated_at": "2026-10-15T09:00:00-04:00"}, "2026-10-15", "source"),
        (
            {"first_published": "garbage", "updated_at": "2026-10-15T09:00:00-04:00"},
            "2026-10-15",
            "source",
        ),
        (
            {"first_published": "2999-01-01T00:00:00Z"},
            "2026-10-20",
            "first seen",
        ),  # the future: ignored
        ({"createdAt": True, "updated_at": None, "publishedAt": ""}, "2026-10-20", "first seen"),
        ({}, "2026-10-20", "first seen"),
    ],
)
def test_the_source_date_is_used_when_readable_else_first_seen(
    payload: dict[str, Any], expected: str, where: str
) -> None:
    found, how = posting_date(job(payload, first_seen=datetime(2026, 10, 20, tzinfo=UTC)))
    assert (found.isoformat(), how) == (expected, where)


def test_a_non_dict_payload_and_huge_timestamps_do_not_crash() -> None:
    odd = Job(raw_payload=[1, 2], first_seen_at=NOW, title="T")  # type: ignore[arg-type]
    assert posting_date(odd)[1] == "first seen"
    assert posting_date(job({"createdAt": 10**30}))[1] == "first seen"


def test_age_is_whole_days_and_never_negative() -> None:
    assert age_days(job({"first_published": "2026-10-01T00:00:00Z"}), NOW) == 21
    assert age_days(job(), NOW) == 0
    assert age_days(job({"first_published": "2026-10-22T23:00:00Z"}), NOW - timedelta(days=3)) == 0


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def seed(session: Session, ages: dict[str, int], scores: dict[str, float]) -> None:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(
        session,
        source,
        [NormalizedJob("Acme", name, f"Job {name}", f"u{name}", "Boston") for name in ages],
    )
    for row in session.scalars(select(Job)):
        name = row.external_id
        row.first_seen_at = NOW - timedelta(days=ages[name])
        session.add(
            Assessment(job_id=row.id, filter_passed=True, fit_score=scores[name], explanation="x")
        )
    session.commit()


def titles(rows: list[Any]) -> list[str]:
    return [j.title for j, _ in rows]


def test_an_old_posting_ranks_a_little_lower_but_keeps_its_score_and_is_not_hidden(
    session: Session,
) -> None:
    seed(session, {"old": 30, "new": 2}, {"old": 82.0, "new": 80.0})
    assert titles(ranked_jobs(session)) == ["Job old", "Job new"]  # no adjustment asked for
    rows = ranked_jobs(session, stale_after_days=21, stale_penalty=5, now=NOW)
    assert titles(rows) == ["Job new", "Job old"]  # 77 < 80
    assert {j.title: a.fit_score for j, a in rows}["Job old"] == 82.0  # stored score untouched
    gentle = ranked_jobs(session, stale_after_days=21, stale_penalty=1, now=NOW)
    assert titles(gentle) == ["Job old", "Job new"]  # 81 > 80: only a nudge
    assert len(ranked_jobs(session, stale_after_days=1, stale_penalty=99, now=NOW)) == 2


def test_the_threshold_boundary_is_strictly_older_than(session: Session) -> None:
    seed(session, {"edge": 21, "over": 22, "fresh": 1}, {"edge": 90.0, "over": 90.0, "fresh": 89.0})
    rows = ranked_jobs(session, stale_after_days=21, stale_penalty=5, now=NOW)
    assert titles(rows) == ["Job edge", "Job fresh", "Job over"]


def test_the_page_shows_age_as_a_small_footnote_and_says_when_it_is_old(session: Session) -> None:
    seed(session, {"old": 24, "new": 0, "one": 1}, {"old": 90.0, "new": 80.0, "one": 70.0})
    entries = {e.title: e for e in gather_shortlist(session, now=NOW).entries}
    assert entries["Job old"].footnotes[0] == "Posted 24 days ago, older than 3 weeks"
    assert entries["Job new"].footnotes[0] == "Posted today"
    assert entries["Job one"].footnotes[0] == "Posted 1 day ago"
    html = render_shortlist(ShortlistData([entries["Job old"]], NOW), NOW)
    assert '<p class="footnote">Posted 24 days ago, older than 3 weeks</p>' in html
    assert "badge" not in html


def test_the_footnote_names_odd_thresholds_in_days(session: Session, tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(Settings(stale_after_days=10).model_dump_json())
    seed(session, {"old": 12}, {"old": 90.0})
    entry = gather_shortlist(session, now=NOW).entries[0]
    assert entry.footnotes[0] == "Posted 12 days ago, older than 10 days"


def test_the_new_settings_are_validated() -> None:
    assert Settings().stale_after_days == 21 and Settings().stale_penalty_points == 5
    for bad in (
        {"stale_after_days": 0},
        {"stale_penalty_points": -1},
        {"stale_penalty_points": 51},
    ):
        with pytest.raises(ValueError):
            Settings(**bad)
