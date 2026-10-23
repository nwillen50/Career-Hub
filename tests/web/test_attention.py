"""The menu-bar state: count, error flag, words (CH-146). Counts only; saved data."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from test_today import add_job  # noqa: F401  (saved-data helper)

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Interaction, Person, utcnow
from careerhub.core.runlog import finish_run, start_run
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.attention import MENU_ITEMS, AttentionState, attention_state
from careerhub.modules.jobs.reactions import react


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_the_count_is_follow_ups_due_plus_top_jobs_not_yet_reacted_to(session: Session) -> None:
    jobs = [add_job(session, n, 90 - n) for n in range(1, 5)]
    person = Person(full_name="Ben", company_id=jobs[0].company_id)
    session.add(person)
    session.flush()
    for days in (1, 2):
        session.add(
            Interaction(
                person_id=person.id,
                occurred_at=utcnow() - timedelta(days=9),
                channel="email",
                follow_up_due=utcnow() - timedelta(days=days),
            )
        )
    session.commit()
    state = attention_state(session)
    assert (state.follow_ups_due, state.new_top_jobs, state.count) == (2, 4, 6)
    react(session, jobs[0].id, "interested")  # any reaction clears a job from the count
    assert attention_state(session).count == 5


def test_a_failed_last_run_sets_the_error_state_and_a_later_success_clears_it(
    session: Session,
) -> None:
    finish_run(session, start_run(session, "nightly"), 0, ConnectionError())
    assert attention_state(session).error
    finish_run(session, start_run(session, "nightly"), 3)
    assert not attention_state(session).error


def test_no_data_means_zero_and_no_error(session: Session) -> None:
    state = attention_state(session)
    assert (state.count, state.error, state.label) == (0, False, "0")


def test_the_label_and_tooltip_say_it_in_words_and_never_by_colour_alone() -> None:
    state = AttentionState(follow_ups_due=2, new_top_jobs=3, error=True)
    assert state.label == "! 5"
    assert state.description == "2 follow-ups due, 3 new top jobs, the last nightly run failed"
    assert AttentionState(0, 1, False).label == "1"


def test_the_json_for_the_helper_has_counts_and_the_one_menu_item_only() -> None:
    data = AttentionState(1, 2, False).as_json()
    assert data["menu"] == list(MENU_ITEMS) == ["Open Today"]
    assert set(data) == {
        "follow_ups_due", "new_top_jobs", "error", "count", "label", "description", "menu"
    }  # fmt: skip


def test_hub_attention_prints_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "d"))
    store = InMemoryKeyStore()
    assert main(["attention"], store) == 0
    assert capsys.readouterr().out.strip() == "0 follow-ups due, 0 new top jobs"
    assert main(["attention", "--json"], store) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["count"] == 0 and printed["error"] is False and printed["menu"] == ["Open Today"]
    assert main(["attention", "--bogus"], store) == 2
