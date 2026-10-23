"""Applications, saved views, and CSV export (CH-142, data side)."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Application, Company, Interaction, Job, Person, Source, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs.applications import (
    VIEWS,
    TrackerError,
    export_csv,
    set_follow_up,
    set_next_step,
    track,
    tracker_rows,
)
from careerhub.modules.jobs.reactions import react

NOW = utcnow()


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def job(session: Session, n: int, company: str = "Acme", deadline: str | None = None) -> Job:
    source = session.query(Source).first()
    if source is None:
        source = Source(kind="greenhouse", name="s", url="u", status="approved")
        session.add(source)
        session.flush()
    firm = session.query(Company).filter_by(name=company).first() or Company(
        name=company, name_key=company.lower()
    )
    row = Job(
        company=firm, source_id=source.id, external_id=str(n), title=f"Role {n}", url="u",
        dedupe_key=f"k{n}", first_seen_at=NOW, last_seen_at=NOW,
        structured_fields={"fields": {"deadline": deadline}},
    )  # fmt: skip
    session.add(row)
    session.commit()
    return row


def keys(rows: list) -> list[str]:  # type: ignore[type-arg]
    return [r.title for r in rows]


def test_tracking_creates_one_application_per_job_and_stamps_the_applied_date(
    session: Session,
) -> None:
    a = job(session, 1)
    first = track(session, a.id, "interested", NOW)
    assert first.status == "interested" and first.applied_at is None
    applied = track(session, a.id, "applied", NOW)
    assert applied.id == first.id and applied.applied_at == NOW
    later = track(session, a.id, "interviewing", NOW + timedelta(days=3))
    assert later.applied_at == NOW  # the date of applying does not move
    assert session.query(Application).count() == 1


def test_bad_status_and_missing_job_are_refused(session: Session) -> None:
    a = job(session, 1)
    with pytest.raises(TrackerError, match="Unknown status"):
        track(session, a.id, "hired")
    with pytest.raises(TrackerError, match="no longer exists"):
        track(session, 999, "applied")
    with pytest.raises(TrackerError):
        set_follow_up(session, 999, NOW)
    with pytest.raises(TrackerError):
        set_next_step(session, 999, "x")
    assert session.query(Application).count() == 0


def test_the_interested_and_applied_reactions_start_tracking(session: Session) -> None:
    a, b, c = job(session, 1), job(session, 2), job(session, 3)
    react(session, a.id, "interested")
    react(session, b.id, "applied")
    react(session, c.id, "not_interested", "pay")
    statuses = {r.title: r.status for r in tracker_rows(session)}
    assert statuses == {"Role 1": "interested", "Role 2": "applied"}


def test_follow_up_and_next_step_can_be_set_and_cleared(session: Session) -> None:
    a = track(session, job(session, 1).id, "applied", NOW)
    set_follow_up(session, a.id, NOW + timedelta(days=5))
    set_next_step(session, a.id, "  email   the recruiter ")
    row = tracker_rows(session)[0]
    assert row.follow_up_due == NOW + timedelta(days=5) and row.next_step == "email the recruiter"
    set_follow_up(session, a.id, None)
    set_next_step(session, a.id, " ")
    row = tracker_rows(session)[0]
    assert row.follow_up_due is None and row.next_step is None


def test_every_saved_view_picks_the_right_applications(session: Session) -> None:
    due = track(session, job(session, 1).id, "applied", NOW - timedelta(days=2))
    set_follow_up(session, due.id, NOW - timedelta(days=1))
    silent = track(session, job(session, 2, "Globex").id, "applied", NOW - timedelta(days=12))
    heard = job(session, 3, "Initech")
    track(session, heard.id, "applied", NOW - timedelta(days=12))
    contact = Person(full_name="Ada", company_id=heard.company_id)
    session.add(contact)
    session.flush()
    session.add(
        Interaction(person_id=contact.id, occurred_at=NOW - timedelta(days=5), channel="email")
    )
    interview = track(session, job(session, 4, "Hooli").id, "interviewing", NOW)
    soon = track(
        session,
        job(session, 5, "Umbrella", deadline=(NOW + timedelta(days=3)).date().isoformat()).id,
        "interested",
        NOW,
    )
    later = track(
        session,
        job(session, 6, "Vandelay", deadline=(NOW + timedelta(days=40)).date().isoformat()).id,
        "interested",
        NOW,
    )
    old = track(session, job(session, 7, "Stark").id, "interested", NOW)
    old.created_at = NOW - timedelta(days=9)
    session.commit()
    track(session, job(session, 8, "Wayne").id, "interested", NOW)
    stark = session.get(Job, old.job_id)
    assert stark is not None
    session.add(Person(full_name="Bo", company_id=stark.company_id, how_known="mentor"))
    session.commit()
    assert keys(tracker_rows(session, "followups_due", now=NOW)) == ["Role 1"]
    assert keys(tracker_rows(session, "applied_no_reply", now=NOW)) == [
        "Role 2"
    ]  # Role 3 heard back
    assert keys(tracker_rows(session, "interviewing", now=NOW)) == ["Role 4"]
    assert keys(tracker_rows(session, "deadlines_week", now=NOW)) == ["Role 5"]
    assert keys(tracker_rows(session, "interested_stale", now=NOW)) == ["Role 7"]
    assert keys(tracker_rows(session, "warm_path", now=NOW)) == ["Role 3", "Role 7"]
    assert keys(tracker_rows(session, "interested", now=NOW)) == [
        "Role 5",
        "Role 6",
        "Role 7",
        "Role 8",
    ]
    assert len(tracker_rows(session, "all", now=NOW)) == 8
    assert {k for k, _ in VIEWS} >= {"all", "followups_due", "warm_path"}
    assert silent.id and later.id and soon.id and interview.id


def test_text_and_status_filters_combine(session: Session) -> None:
    track(session, job(session, 1, "Acme Robotics").id, "applied")
    track(session, job(session, 2, "Globex").id, "interested")
    track(session, job(session, 3, "Acme Labs").id, "interested")
    assert keys(tracker_rows(session, text="acme")) == ["Role 1", "Role 3"]
    assert keys(tracker_rows(session, text="ACME", statuses=["interested"])) == ["Role 3"]
    assert keys(tracker_rows(session, statuses=["applied", "interested"])) == [
        "Role 1",
        "Role 2",
        "Role 3",
    ]
    assert tracker_rows(session, text="nothing") == []
    with pytest.raises(TrackerError, match="Unknown view"):
        tracker_rows(session, "mystery")


def test_csv_export_is_plain_and_safe_to_open_in_a_spreadsheet(session: Session) -> None:
    evil = job(session, 1, '=HYPERLINK("http://evil")')
    track(session, evil.id, "applied", NOW)
    set_next_step(session, session.query(Application).one().id, "+cmd|' /C calc'!A0")
    text = export_csv(tracker_rows(session))
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Company", "Role", "Status", "Applied", "Follow-up", "Next step"]
    assert rows[1][0].startswith("'=") and rows[1][5].startswith("'+")
    assert rows[1][2] == "Applied" and rows[1][3] == NOW.date().isoformat()
    assert export_csv([]).count("\n") == 1  # just the header


def test_a_reaction_never_moves_a_tracked_job_backwards(session: Session) -> None:
    """Review: Interested on a job you are interviewing for reset it to Interested."""
    a = job(session, 1)
    track(session, a.id, "interviewing", NOW)
    react(session, a.id, "interested")
    assert tracker_rows(session)[0].status == "interviewing"
    react(session, a.id, "applied")
    assert tracker_rows(session)[0].status == "interviewing"


def test_not_interested_and_undo_take_a_reaction_made_application_back_out(
    session: Session,
) -> None:
    from careerhub.modules.jobs.reactions import undo

    a, b, c = job(session, 1), job(session, 2), job(session, 3)
    react(session, a.id, "interested")
    react(session, a.id, "not_interested", "pay")
    assert tracker_rows(session) == []  # changed your mind: no longer tracked
    row = react(session, b.id, "applied")
    assert [r.status for r in tracker_rows(session)] == ["applied"]
    assert undo(session, b.id, row.id)
    assert tracker_rows(session) == []  # the undo took the application back too
    kept = track(session, c.id, "applied", NOW)
    set_next_step(session, kept.id, "email the recruiter")
    react(session, c.id, "not_interested")
    assert [r.title for r in tracker_rows(session)] == ["Role 3"]  # your own notes keep it


def test_closed_applications_do_not_show_in_the_follow_up_or_deadline_views(
    session: Session,
) -> None:
    soon = (NOW + timedelta(days=2)).date().isoformat()
    open_one = track(session, job(session, 1, deadline=soon).id, "applied", NOW)
    closed = track(session, job(session, 2, deadline=soon).id, "rejected", NOW)
    for application in (open_one, closed):
        set_follow_up(session, application.id, NOW - timedelta(days=1))
    assert keys(tracker_rows(session, "followups_due", now=NOW)) == ["Role 1"]
    assert keys(tracker_rows(session, "deadlines_week", now=NOW)) == ["Role 1"]


def test_a_deadline_with_a_time_counts_on_its_day(session: Session) -> None:
    """Review: '2026-10-30T17:00:00' was compared as text and dropped on its last day."""
    last_day = (NOW + timedelta(days=7)).date().isoformat() + "T17:00:00"
    track(session, job(session, 1, deadline=last_day).id, "interested", NOW)
    assert keys(tracker_rows(session, "deadlines_week", now=NOW)) == ["Role 1"]
