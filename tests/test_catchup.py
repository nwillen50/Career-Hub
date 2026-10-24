"""Catching up a night skipped on battery (CH-181). Fake clock, fake power reader, no launchd."""

from __future__ import annotations

import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from careerhub import cli
from careerhub.catchup import catchup_note, decide, record_skip
from careerhub.core.migrate import migrate
from careerhub.core.models import RunLog
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.schedule import CATCHUP_INTERVAL_SECONDS, CATCHUP_LABEL, render_plist

NEW_YORK = ZoneInfo("America/New_York")
SETTINGS = Settings()  # America/New_York, catchup_until 08:00
# Oct 24 2026, 2:00 a.m. in New York (EDT, UTC-4) is 06:00 UTC.
SKIPPED_AT = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)


def at(hour: int, minute: int = 0) -> datetime:
    """A moment on Oct 24 2026 at this local New York time."""
    return datetime(2026, 10, 24, hour, minute, tzinfo=NEW_YORK).astimezone(UTC)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def run_row(session: Session, started: datetime, status: str = "succeeded") -> None:
    session.add(RunLog(step="nightly", status=status, started_at=started, finished_at=started))
    session.commit()


def test_skipped_then_plugged_in_before_the_cutoff_runs(session: Session) -> None:
    record_skip(session, SKIPPED_AT)
    decision = decide(session, at(6, 30), "ac", SETTINGS)
    assert decision.run and "plugged in" in decision.reason


def test_still_on_battery_or_an_unreadable_power_source_does_nothing(session: Session) -> None:
    record_skip(session, SKIPPED_AT)
    assert not decide(session, at(6, 30), "battery", SETTINGS).run
    unreadable = decide(session, at(6, 30), "unknown", SETTINGS)
    assert not unreadable.run and "could not be read" in unreadable.reason


def test_after_the_cutoff_does_nothing_and_the_cutoff_is_a_setting(session: Session) -> None:
    record_skip(session, SKIPPED_AT)
    late = decide(session, at(8, 0), "ac", SETTINGS)
    assert not late.run and "08:00" in late.reason
    assert decide(session, at(8, 30), "ac", Settings(catchup_until="09:00")).run
    assert not decide(session, at(7, 59), "ac", Settings(catchup_until="07:30")).run


def test_no_skip_tonight_means_nothing_to_catch_up(session: Session) -> None:
    assert not decide(session, at(6, 30), "ac", SETTINGS).run  # no skip at all
    record_skip(session, SKIPPED_AT - timedelta(days=1))
    assert not decide(session, at(6, 30), "ac", SETTINGS).run  # last night's skip is old news


def test_it_runs_once_not_twice(session: Session) -> None:
    record_skip(session, SKIPPED_AT)
    assert decide(session, at(6, 30), "ac", SETTINGS).run
    run_row(session, at(6, 31), "succeeded")
    again = decide(session, at(7, 0), "ac", SETTINGS)
    assert not again.run and "already happened" in again.reason


def test_a_run_in_progress_blocks_a_catch_up(session: Session) -> None:
    record_skip(session, SKIPPED_AT)
    run_row(session, at(6, 20), "running")
    assert decide(session, at(6, 30), "ac", SETTINGS).reason == "a run is in progress"


def test_a_run_before_the_skip_does_not_count(session: Session) -> None:
    run_row(session, SKIPPED_AT - timedelta(hours=20))
    record_skip(session, SKIPPED_AT)
    assert decide(session, at(6, 30), "ac", SETTINGS).run


def test_the_status_line_follows_the_story(session: Session) -> None:
    assert catchup_note(session, NEW_YORK) is None
    record_skip(session, SKIPPED_AT)
    assert catchup_note(session, NEW_YORK) == (
        "Skipped on battery at Sat Oct 24, 02:00; no catch-up has run yet."
    )
    run_row(session, at(6, 40))
    assert (
        catchup_note(session, NEW_YORK)
        == "Skipped on battery at Sat Oct 24, 02:00, caught up at 06:40."
    )


def test_the_catchup_job_looks_every_30_minutes_and_runs_the_catch_up_command(
    tmp_path: Path,
) -> None:
    data = plistlib.loads(
        render_plist("00:00", python="/venv/bin/python", logs=tmp_path, command="catchup")
    )
    assert data["Label"] == CATCHUP_LABEL == "com.careerhub.catchup"
    assert data["ProgramArguments"] == [
        "/venv/bin/python",
        "-m",
        "careerhub.cli",
        "nightly",
        "--catch-up",
    ]
    assert data["StartInterval"] == CATCHUP_INTERVAL_SECONDS == 1800
    assert "StartCalendarInterval" not in data and data["RunAtLoad"] is False


def test_schedule_install_writes_the_catchup_job_with_the_power_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    assert cli.main(["schedule", "install"], InMemoryKeyStore()) == 0
    out = capsys.readouterr().out
    assert "com.careerhub.catchup.plist" in out and "launchctl bootout" in out
    assert (tmp_path / "Library" / "LaunchAgents" / "com.careerhub.catchup.plist").exists()


def test_on_battery_the_command_records_the_skip_and_the_catch_up_command_acts_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    store = InMemoryKeyStore()
    ran: list[str] = []

    class Report:
        problems: list[str] = []

        def summary(self) -> str:
            return "done"

    monkeypatch.setattr(cli, "run_nightly", lambda *a, **k: ran.append("ran") or Report())
    monkeypatch.setattr("careerhub.catchup.utcnow", lambda: SKIPPED_AT)  # a fixed clock
    assert cli._nightly(store, if_plugged_in=True, power=lambda: "battery") == 0
    # Still on battery: the catch-up does nothing.
    cli._nightly(store, catch_up=True, power=lambda: "battery", now=at(6, 30))
    assert "nothing to do" in capsys.readouterr().out and ran == []
    # Plugged in: the full nightly runs once.
    cli._nightly(store, catch_up=True, power=lambda: "ac", now=at(6, 30))
    assert ran == ["ran"] and "running tonight's skipped run" in capsys.readouterr().out
