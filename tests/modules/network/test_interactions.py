"""Logging contacts, follow-ups and editing how you know someone (CH-169). Sample data only."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Company, Interaction, Person, utcnow
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.network import (
    InteractionError,
    UnknownPerson,
    log_interaction,
    recent_interactions,
    set_follow_up_done,
    set_own_words,
)
from careerhub.web.today import gather_today


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


@pytest.fixture
def ada(session: Session) -> Person:
    person = Person(
        full_name="Ada Example",
        company=Company(name="Acme", name_key="acme"),
        how_known="former manager; alumni: Tufts Jumbos",
    )
    session.add(person)
    session.commit()
    return person


def test_logging_a_contact_with_a_follow_up(session: Session, ada: Person) -> None:
    due = utcnow() + timedelta(days=7)
    row = log_interaction(session, ada.id, "email", "  Sent thanks  ", follow_up_due=due)
    assert (row.summary, row.channel, row.follow_up_due, row.follow_up_done) == (
        "Sent thanks", "email", due, False,
    )  # fmt: skip
    assert recent_interactions(session) == [row]


def test_bad_contacts_are_refused(session: Session, ada: Person) -> None:
    with pytest.raises(UnknownPerson):
        log_interaction(session, 999, "email")
    with pytest.raises(InteractionError, match="channel"):
        log_interaction(session, ada.id, "carrier pigeon")
    with pytest.raises(InteractionError, match="1000"):
        log_interaction(session, ada.id, "call", "x" * 1001)
    assert recent_interactions(session) == []


def test_a_done_follow_up_leaves_today_and_can_come_back(session: Session, ada: Person) -> None:
    now = utcnow()
    row = log_interaction(
        session, ada.id, "email", occurred_at=now - timedelta(days=9),
        follow_up_due=now - timedelta(days=2),
    )  # fmt: skip
    assert len(gather_today(session, now).follow_ups) == 1
    set_follow_up_done(session, row.id)
    assert gather_today(session, now).follow_ups == []
    set_follow_up_done(session, row.id, done=False)
    assert len(gather_today(session, now).follow_ups) == 1


def test_done_needs_a_follow_up_date_and_a_real_interaction(session: Session, ada: Person) -> None:
    plain = log_interaction(session, ada.id, "call")
    with pytest.raises(InteractionError, match="no follow-up"):
        set_follow_up_done(session, plain.id)
    with pytest.raises(InteractionError, match="gone"):
        set_follow_up_done(session, 999)


def test_editing_how_you_know_someone_keeps_imported_alumni_tags(
    session: Session, ada: Person
) -> None:
    set_own_words(session, ada.id, "met at a career fair; later mentor")
    assert ada.how_known == "met at a career fair, later mentor; alumni: Tufts Jumbos"
    set_own_words(session, ada.id, "   ")
    assert ada.how_known == "alumni: Tufts Jumbos"


def test_how_you_know_someone_can_be_cleared_and_is_length_limited(session: Session) -> None:
    plain = Person(full_name="Bo Example", how_known="old friend")
    session.add(plain)
    session.commit()
    set_own_words(session, plain.id, "")
    assert plain.how_known is None
    with pytest.raises(InteractionError, match="too long"):
        set_own_words(session, plain.id, "x" * 501)
    assert plain.how_known is None
    with pytest.raises(UnknownPerson):
        set_own_words(session, 999, "x")


def test_existing_databases_gain_the_column_with_everything_not_done(session: Session) -> None:
    other = Person(full_name="Cy Example")
    session.add(other)
    session.flush()
    session.add(Interaction(person_id=other.id, occurred_at=utcnow(), channel="call"))
    session.commit()
    assert session.query(Interaction).one().follow_up_done is False


def test_a_follow_up_dated_today_is_accepted_and_shows_as_due(
    session: Session, ada: Person
) -> None:
    """Review: today's date is the start of today, which is earlier than now."""
    start_of_today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    log_interaction(session, ada.id, "call", follow_up_due=start_of_today)
    assert len(gather_today(session, utcnow()).follow_ups) == 1
