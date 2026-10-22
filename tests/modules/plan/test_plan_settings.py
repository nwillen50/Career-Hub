"""Tests for the plan's settings (CH-104). All data is made up."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.plan.suggest import DAYS, suggest_week, working_windows
from careerhub.modules.plan.weekly import PlanTask, plan_week
from careerhub.profile.files import load_preferences
from careerhub.profile.models import PlanSettings, Preferences, WorkingHours

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # a Monday, 8:00 in New York


def test_a_missing_plan_section_keeps_todays_values() -> None:
    plan = Preferences().plan
    assert (plan.apply_minutes, plan.outreach_minutes, plan.daily_share) == (90, 30, 0.5)
    assert (plan.min_gap_minutes, plan.tasks_per_plan) == (30, 5)
    hours = plan.working_hours
    assert (hours.start, hours.end, hours.weekdays) == ("09:00", "21:00", {})
    assert not plan.model_fields_set


def test_overrides_load_from_yaml_and_report_which_fields_were_set(tmp_path: Path) -> None:
    path = tmp_path / "preferences.yaml"
    path.write_text(
        "plan:\n  apply_minutes: 60\n  tasks_per_plan: 3\n"
        "  working_hours: {start: '10:00', end: '18:00'}\n",
        encoding="utf-8",
    )
    prefs = load_preferences(path)
    assert prefs is not None
    assert prefs.plan.apply_minutes == 60 and prefs.plan.outreach_minutes == 30
    assert prefs.plan.model_fields_set == {"apply_minutes", "tasks_per_plan", "working_hours"}


def test_weekday_windows_with_a_day_off_and_shorter_weekends() -> None:
    hours = WorkingHours.model_validate(
        {"weekdays": {"sat": {"start": "11:00", "end": "17:00"}, "sun": "off"}}
    )
    windows = working_windows(date(2026, 10, 19), NY, hours)  # Mon..Sun
    assert len(windows) == 6  # Sunday is a day off
    saturday = windows[-1].start.astimezone(NY)
    assert (saturday.weekday(), saturday.hour) == (5, 11)
    assert windows[0].start.astimezone(NY).hour == 9 and windows[0].end.astimezone(NY).hour == 21
    assert DAYS == 7


@pytest.mark.parametrize("off", ["off", "null", "false"])
def test_a_day_off_can_be_written_in_yaml_forms(off: str, tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(f"plan:\n  working_hours:\n    weekdays:\n      sun: {off}\n", encoding="utf-8")
    prefs = load_preferences(path)
    assert prefs is not None and prefs.plan.working_hours.for_day("sun") is None
    assert prefs.plan.working_hours.for_day("mon") == (time(9), time(21))


@pytest.mark.parametrize(
    "bad",
    [
        {"apply_minutes": 5},
        {"apply_minutes": 9999},
        {"daily_share": 0},
        {"daily_share": 1.5},
        {"min_gap_minutes": 1},
        {"tasks_per_plan": 0},
        {"working_hours": {"start": "9am", "end": "17:00"}},
        {"working_hours": {"start": "17:00", "end": "09:00"}},
        {"working_hours": {"start": "09:00", "end": "25:00"}},
        {"working_hours": {"weekdays": {"sat": {"start": "12:00", "end": "11:00"}}}},
        {"working_hours": {"weekdays": {"funday": {"start": "09:00", "end": "10:00"}}}},
        {"nonsense": 1},
    ],
)
def test_invalid_values_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlanSettings.model_validate(bad)


def test_a_bad_plan_section_gives_a_readable_file_error(tmp_path: Path) -> None:
    from careerhub.profile import ProfileFileError

    path = tmp_path / "p.yaml"
    path.write_text("plan:\n  working_hours: {start: '9am'}\n", encoding="utf-8")
    with pytest.raises(ProfileFileError) as raised:
        load_preferences(path)
    assert "plan" in str(raised.value) and "9am" not in str(raised.value)


def test_the_plan_uses_the_configured_gap_share_and_hours() -> None:
    gap_start = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
    from careerhub.modules.plan.freebusy import Interval, free_windows

    free = free_windows([], [Interval(gap_start, gap_start.replace(hour=14, minute=20))], 90)
    assert free == []  # an 80-minute gap is shorter than a configured 90-minute minimum
    tasks = [PlanTask("a", 60, 1.0), PlanTask("b", 60, 0.5)]
    wide = [
        Interval(gap_start, gap_start.replace(hour=20)),
        Interval(gap_start.replace(day=21), gap_start.replace(day=21, hour=20)),
    ]
    even = plan_week(tasks, wide, 240, NY, daily_share=0.25)  # at most 60 minutes a day
    assert len({b.start.date() for b in even.blocks}) == 2


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def test_suggest_week_with_every_day_off_is_an_empty_plan(session: Session) -> None:
    hours = {d: "off" for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    plan = PlanSettings.model_validate({"working_hours": {"weekdays": hours}})
    result = suggest_week(session, Settings(), 5.0, NOW, plan)
    assert result.blocks == [] and result.unplaced == []


def test_hub_plan_says_which_values_are_yours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "preferences.yaml").write_text("plan:\n  apply_minutes: 45\n")
    assert main(["plan"], InMemoryKeyStore()) == 0
    out = capsys.readouterr().out
    assert "apply minutes 45 (yours)" in out and "outreach minutes 30 (default)" in out
    assert "hours 09:00-21:00 (default)" in out
