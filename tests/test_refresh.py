"""The light midday refresh and its launchd job (CH-165). Fake sources and models only."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from test_pipeline import NOW, SETTINGS, env, handler  # noqa: F401  (env is a fixture)

from careerhub.cli import main
from careerhub.core.models import Assessment, Job, ProfileExtraction, RunLog
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.settings import Settings
from careerhub.core.storage import session_factory
from careerhub.pipeline import run_refresh
from careerhub.schedule import REFRESH_LABEL, install_plist, render_plist


def test_refresh_reads_and_filters_new_jobs_but_never_scores_or_backs_up(env: Any) -> None:  # noqa: F811
    engine, data = env
    report = run_refresh(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert (report.new_jobs, report.extracted, report.filtered_out) == (2, 1, 1)
    assert report.scored == 0 and report.tailored == 0 and report.profile_facts == 0
    assert report.problems == []
    assert (data / "shortlist" / "index.html").exists()
    assert not (data / "backups").exists() or not list((data / "backups").glob("*.db"))
    with session_factory(engine)() as session:
        run = session.scalars(select(RunLog)).one()
        assert (run.step, run.status) == ("refresh", "succeeded")
        assessments = session.scalars(select(Assessment)).all()
        assert all(a.fit_score is None and a.tailoring is None for a in assessments)
        assert session.scalars(select(ProfileExtraction)).all() == []
        assert session.scalars(select(Job)).all()


def test_a_paid_model_is_never_used_by_the_refresh(env: Any) -> None:  # noqa: F811
    engine, _ = env
    paid = Settings.model_validate(
        {
            "providers": [
                {
                    "name": "paid",
                    "kind": "openai",
                    "model": "m",
                    "base_url": "https://api.example.com/v1",
                    "input_per_mtok": 1,
                    "output_per_mtok": 2,
                }
            ],
            "model_routes": {"extract": ["paid"]},
        }
    )
    store = InMemoryKeyStore()
    store.set("api-key-paid", "k")
    seen: list[str] = []
    client = handler()
    original = client._transport.handle_request  # type: ignore[attr-defined]

    def spy(request: Any) -> Any:
        seen.append(request.url.host)
        return original(request)

    client._transport.handle_request = spy  # type: ignore[attr-defined]
    report = run_refresh(engine, store, paid, client, NOW)
    assert report.extracted == 0
    assert "api.example.com" not in seen
    assert report.new_jobs == 2  # jobs are still fetched and filtered


def test_a_rerun_of_the_refresh_adds_nothing(env: Any) -> None:  # noqa: F811
    engine, _ = env
    run_refresh(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    again = run_refresh(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert (again.new_jobs, again.extracted) == (0, 0)


def test_the_refresh_plist_runs_hub_refresh_with_its_own_label(tmp_path: Path) -> None:
    data = plistlib.loads(
        render_plist("12:30", python="/p/python", logs=tmp_path, command="refresh")
    )
    assert data["Label"] == REFRESH_LABEL == "com.careerhub.refresh"
    assert data["ProgramArguments"] == ["/p/python", "-m", "careerhub.cli", "refresh"]
    assert data["StartCalendarInterval"] == {"Hour": 12, "Minute": 30}
    assert data["StandardOutPath"].endswith("refresh.out.log")
    path = install_plist("12:30", folder=tmp_path / "LA", command="refresh")
    assert path.name == "com.careerhub.refresh.plist"


def test_an_unknown_job_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        render_plist("12:30", logs=tmp_path, command="bogus")


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: str) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(settings)
    assert main(["schedule", "install"], InMemoryKeyStore()) == 0
    return tmp_path / "Library" / "LaunchAgents"


def test_the_refresh_job_is_off_until_the_setting_turns_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = _install(tmp_path, monkeypatch, "{}")
    assert sorted(p.name for p in agents.iterdir()) == [
        "com.careerhub.catchup.plist",  # CH-181: written while the nightly requires power
        "com.careerhub.nightly.plist",
    ]


def test_turning_it_on_writes_a_second_plist_without_starting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    agents = _install(
        tmp_path, monkeypatch, '{"midday_refresh_on": true, "midday_refresh_time": "13:15"}'
    )
    out = capsys.readouterr().out
    refresh = plistlib.loads((agents / "com.careerhub.refresh.plist").read_bytes())
    assert refresh["StartCalendarInterval"] == {"Hour": 13, "Minute": 15}
    assert out.count("It is not running yet") == 3  # nightly, refresh, and the catch-up job
    assert "bootout" in out and "com.careerhub.refresh" in out
