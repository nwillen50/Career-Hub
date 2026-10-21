from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.cli import main
from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Job, Person, Source
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.plan import Interval, PlanTask, build_tasks, plan_week, suggest_week

NY = ZoneInfo("America/New_York")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def gap(day: int, start: int, end: int) -> Interval:
    return Interval(at(day, start), at(day, end))


def test_urgent_tasks_go_first_and_blocks_are_in_time_order() -> None:
    tasks = [
        PlanTask("later", 60, priority=90),
        PlanTask("soon", 60, priority=10, due=at(22, 23)),
        PlanTask("best", 60, priority=95),
    ]
    plan = plan_week(tasks, [gap(21, 13, 20)], budget_minutes=600, zone=NY)
    assert [b.task.title for b in plan.blocks] == ["soon", "best", "later"]
    assert plan.blocks[0].start == at(21, 13) and plan.blocks[1].start == at(21, 14)


def test_the_weekly_budget_is_a_hard_limit_and_the_rest_is_reported() -> None:
    tasks = [PlanTask(f"t{i}", 60, priority=100 - i) for i in range(5)]
    free = [gap(21, 13, 20), gap(22, 13, 20), gap(23, 13, 20)]
    plan = plan_week(tasks, free, budget_minutes=180, zone=NY)
    assert plan.planned_minutes <= 180 and len(plan.blocks) == 3
    assert [u.reason for u in plan.unplaced] == ["the weekly time budget is used up"] * 2


def test_no_day_gets_more_than_half_the_budget() -> None:
    tasks = [PlanTask(f"t{i}", 60, priority=100 - i) for i in range(4)]
    free = [gap(21, 13, 22), gap(22, 13, 22)]
    plan = plan_week(tasks, free, budget_minutes=240, zone=NY)  # cap: 120 minutes a day
    days = [b.start.date() for b in plan.blocks]
    assert days.count(days[0]) == 2 and len(set(days)) == 2


def test_a_task_never_lands_after_its_deadline() -> None:
    task = PlanTask("urgent", 60, due=at(21, 15))
    plan = plan_week([task], [gap(22, 13, 20)], budget_minutes=600, zone=NY)
    assert plan.blocks == [] and plan.unplaced[0].reason == "no free time before its deadline"


def test_a_task_longer_than_every_gap_is_reported() -> None:
    plan = plan_week([PlanTask("long", 120)], [gap(21, 13, 14)], 600, NY)
    assert plan.unplaced[0].reason == "no free time is long enough"


def test_nothing_overlaps_and_everything_sits_in_free_time() -> None:
    tasks = [PlanTask(f"t{i}", 45, priority=i) for i in range(6)]
    free = [gap(21, 13, 15), gap(22, 14, 17), gap(23, 13, 14)]
    plan = plan_week(tasks, free, 1000, NY)
    for first, second in zip(plan.blocks, plan.blocks[1:], strict=False):
        assert first.end <= second.start
    for block in plan.blocks:
        assert any(g.start <= block.start and block.end <= g.end for g in free)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def ranked(session: Session, deadline: str | None = None) -> Job:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Engineer", "u", "Boston", "T")])
    job = session.scalars(select(Job)).one()
    job.structured_fields = {"fields": {"deadline": deadline}}
    session.add(Assessment(job_id=job.id, filter_passed=True, fit_score=80.0, criteria_scores=[]))
    session.commit()
    return job


def test_tasks_come_from_the_shortlist_with_outreach_when_you_know_someone(
    session: Session,
) -> None:
    job = ranked(session, deadline="2026-10-30")
    session.add(Person(full_name="Ada Sample", company_id=job.company_id, how_known="friend"))
    session.commit()
    titles = [(t.title, t.minutes) for t in build_tasks(session, NY)]
    assert ("Apply: Engineer at Acme", 90) in titles
    assert ("Message Ada Sample about Acme", 30) in titles
    apply_task = next(t for t in build_tasks(session, NY) if t.title.startswith("Apply"))
    assert apply_task.due == datetime(2026, 10, 31, 3, 59, tzinfo=UTC)  # 23:59 New York (EDT)


def test_no_outreach_task_for_someone_with_no_ties_and_a_bad_deadline_is_ignored(
    session: Session,
) -> None:
    job = ranked(session, deadline="soonish")
    session.add(Person(full_name="Cy Nobody", company_id=job.company_id))
    session.commit()
    tasks = build_tasks(session, NY)
    assert [t.title for t in tasks] == ["Apply: Engineer at Acme"] and tasks[0].due is None


def test_suggest_week_uses_free_time_from_now_and_avoids_calendar_busy_time(
    session: Session, tmp_path: Path
) -> None:
    ranked(session)
    ics = tmp_path / "cal.ics"
    ics.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\nUID:1\nDTSTAMP:20261001T000000Z\n"
        "DTSTART;TZID=America/New_York:20261021T090000\nDTEND;TZID=America/New_York:20261021T210000\n"
        "SUMMARY:All day workshop\nEND:VEVENT\nEND:VCALENDAR\n",
        encoding="utf-8",
    )
    settings = Settings(timezone="America/New_York", calendar_files=(str(ics),))
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # 8am in New York, Wednesday
    plan = suggest_week(session, settings, budget_hours=3, now=now)
    assert len(plan.blocks) == 1
    start = plan.blocks[0].start.astimezone(NY)
    assert start.date() > now.astimezone(NY).date()  # Wednesday is fully busy
    assert start >= now and plan.blocks[0].start - now < timedelta(days=7)


def test_hub_plan_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    store = InMemoryKeyStore()
    assert main(["plan"], store) == 0
    assert "Nothing to plan yet" in capsys.readouterr().out
    (tmp_path / "settings.json").write_text(json.dumps({"calendar_files": ["/nope/missing.ics"]}))
    assert main(["plan"], store) == 1
    assert "Can't read a calendar file" in capsys.readouterr().out
