"""Applications that go silent become Ghosted on the nightly run, and it is reversible (CH-168)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session
from test_applications import NOW, job, session  # noqa: F401  (session is a fixture)

from careerhub.core.models import Application, Interaction, Person, utcnow
from careerhub.modules.jobs.applications import mark_ghosted, track

LATER = NOW + timedelta(days=31)


def status(session: Session, application_id: int) -> str:  # noqa: F811
    session.expire_all()
    row = session.get(Application, application_id)
    assert row is not None
    return row.status


def test_a_silent_applied_application_becomes_ghosted(session: Session) -> None:  # noqa: F811
    app = track(session, job(session, 1).id, "applied", NOW)
    assert mark_ghosted(session, 30, LATER) == 1
    assert status(session, app.id) == "ghosted"


def test_a_recent_application_is_left_alone(session: Session) -> None:  # noqa: F811
    app = track(session, job(session, 1).id, "applied", NOW)
    assert mark_ghosted(session, 30, NOW + timedelta(days=29)) == 0
    assert status(session, app.id) == "applied"


def test_only_applied_applications_are_ever_ghosted(session: Session) -> None:  # noqa: F811
    ids = [
        track(session, job(session, n, f"Co{n}").id, s, NOW).id
        for n, s in enumerate(["interested", "reached_out", "interviewing", "offer"], 1)
    ]
    assert mark_ghosted(session, 30, LATER) == 0
    assert [status(session, i) for i in ids] == [
        "interested", "reached_out", "interviewing", "offer"
    ]  # fmt: skip


def test_contact_at_the_company_within_the_window_keeps_it_alive(
    session: Session,  # noqa: F811
) -> None:
    posting = job(session, 1)
    app = track(session, posting.id, "applied", NOW)
    ada = Person(full_name="Ada", company_id=posting.company_id)
    session.add(ada)
    session.flush()
    session.add(
        Interaction(person_id=ada.id, occurred_at=LATER - timedelta(days=5), channel="email")
    )
    session.commit()
    assert mark_ghosted(session, 30, LATER) == 0
    assert status(session, app.id) == "applied"


def test_contact_at_another_company_does_not_count(session: Session) -> None:  # noqa: F811
    app = track(session, job(session, 1, "Acme").id, "applied", NOW)
    other = job(session, 2, "Globex")
    bob = Person(full_name="Bob", company_id=other.company_id)
    session.add(bob)
    session.flush()
    session.add(Interaction(person_id=bob.id, occurred_at=LATER, channel="email"))
    session.commit()
    assert mark_ghosted(session, 30, LATER) == 1
    assert status(session, app.id) == "ghosted"


def test_changing_the_status_back_is_not_undone_by_the_next_run(
    session: Session,  # noqa: F811
) -> None:
    app = track(session, job(session, 1).id, "applied", NOW)
    mark_ghosted(session, 30, LATER)
    track(session, app.job_id, "applied")  # the user changes it back, in real time (updated_at)
    reverted = utcnow()
    assert mark_ghosted(session, 30, reverted + timedelta(days=1)) == 0
    assert status(session, app.id) == "applied"
    assert mark_ghosted(session, 30, reverted + timedelta(days=31)) == 1
