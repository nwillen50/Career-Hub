from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Job, LLMCacheEntry, RunLog
from careerhub.core.secrets import InMemoryKeyStore, new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.modules.jobs import approve_source, propose_source
from careerhub.pipeline import run_nightly

NOW = datetime(2026, 10, 21, 6, 0, tzinfo=UTC)
BOARD = {
    "jobs": [
        {
            "id": 1,
            "title": "ML Engineer, New Grad",
            "absolute_url": "https://example.com/1",
            "location": {"name": "Boston, MA"},
            "content": "Build ML systems.",
        },
        {
            "id": 2,
            "title": "Senior Sales Director",
            "absolute_url": "https://example.com/2",
            "location": {"name": "Dallas, TX"},
            "content": "Lead sales.",
        },
    ]
}
SETTINGS = Settings.model_validate(
    {
        "providers": [{"name": "local", "kind": "local", "model": "m"}],
        "model_routes": {"extract": ["local"], "score": ["local"]},
    }
)
TAILOR = json.dumps({"lead_with": ["Your ML project"], "gaps": [], "resume_suggestions": []})
EXTRACT = json.dumps({"level": "new_grad", "locations": ["Boston, MA"]})
SCORE = json.dumps(
    {"criteria": [{"name": "field", "score": 8, "reason": "fits"}], "explanation": "Good fit."}
)


def handler(board_status: int = 200, model_ok: bool = True, tokens: int = 0):  # type: ignore[no-untyped-def]
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(board_status, content=json.dumps(BOARD))
        if not model_ok:
            return httpx.Response(500)
        prompt = json.loads(request.content)["messages"][-1]["content"]
        if "extract structured facts" in prompt:
            text = EXTRACT
        elif "tailor their job application" in prompt:
            text = TAILOR
        else:
            text = SCORE
        body = {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0},
        }
        return httpx.Response(200, content=json.dumps(body))

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "profile.md").write_text(
        "---\nname: Jane Sample\nskills: [Python]\n---\n"
    )
    (tmp_path / "profile" / "preferences.yaml").write_text(
        "hard_constraints:\n  excluded_title_words: [senior]\n"
    )
    (tmp_path / "profile" / "rubric.yaml").write_text(
        "version: 1\ncriteria:\n  - {name: field, weight: 1, description: d}\n"
    )
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    with session_factory(engine)() as session:
        source = propose_source(
            session, "greenhouse", "Acme", "https://boards.greenhouse.io/acme", {"board": "acme"}
        )
        approve_source(session, source.id)
    return engine, tmp_path


def test_the_whole_pipeline_runs_and_writes_the_page(env: Any) -> None:
    engine, data = env
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert (report.sources_ok, report.new_jobs, report.extracted) == (1, 2, 2)
    assert (report.filtered_out, report.scored, report.problems) == (1, 1, [])
    page = (data / "shortlist" / "index.html").read_text(encoding="utf-8")
    assert "ML Engineer, New Grad" in page and "Good fit." in page
    assert "Senior Sales Director" not in page
    with session_factory(engine)() as session:
        run = session.scalars(select(RunLog)).one()
        assert (run.step, run.status, run.items_processed) == ("nightly", "succeeded", 1)
        rejected = session.scalars(
            select(Assessment).join(Job).where(Job.title.like("Senior%"))
        ).one()
        assert not rejected.filter_passed
    assert list((data / "backups").glob("careerhub-*.db"))


def test_a_rerun_adds_nothing_and_asks_the_model_nothing_new(env: Any) -> None:
    engine, _ = env
    run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    again = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert (again.new_jobs, again.extracted, again.scored) == (0, 0, 0)


def test_when_every_source_fails_the_run_is_marked_failed_and_the_page_says_so(env: Any) -> None:
    engine, data = env
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(board_status=503), NOW)
    assert report.sources_failed == 1 and any("run failed" in p for p in report.problems)
    with session_factory(engine)() as session:
        assert session.scalars(select(RunLog)).one().status == "failed"
    assert "The last nightly run failed" in (data / "shortlist" / "index.html").read_text()


def test_one_failing_source_does_not_stop_the_others(env: Any) -> None:
    engine, _ = env
    with session_factory(engine)() as session:
        bad = propose_source(session, "greenhouse", "Bad", "https://boards.greenhouse.io/bad", {})
        approve_source(session, bad.id)  # no board in its config: always fails
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert (report.sources_ok, report.sources_failed, report.scored) == (1, 1, 1)
    with session_factory(engine)() as session:
        assert session.scalars(select(RunLog)).one().status == "succeeded"


def test_model_failures_skip_jobs_but_the_page_is_still_written(env: Any) -> None:
    engine, data = env
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(model_ok=False), NOW)
    assert report.scored == 0 and any("could not be understood" in p for p in report.problems)
    assert (data / "shortlist" / "index.html").exists()


def test_no_configured_models_skips_the_model_stages_and_says_so(env: Any) -> None:
    engine, _ = env
    report = run_nightly(engine, InMemoryKeyStore(), Settings(), handler(), NOW)
    assert report.new_jobs == 2 and report.scored == 0
    assert any("no usable model for 'extract'" in p for p in report.problems)
    assert any("no usable model for 'score'" in p for p in report.problems)


def test_a_missing_profile_skips_scoring_but_still_filters(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, data = env
    (data / "profile" / "rubric.yaml").unlink()
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert report.scored == 0 and report.filtered_out == 1
    assert any("rubric.yaml is missing" in p for p in report.problems)


def test_the_budget_pauses_model_work_but_not_the_run(env: Any) -> None:
    engine, _ = env
    paid = Settings.model_validate(
        {
            "monthly_budget_usd": 0.5,
            "providers": [
                {
                    "name": "cloud",
                    "kind": "openai",
                    "model": "m",
                    "input_per_mtok": 1,
                    "output_per_mtok": 1,
                }
            ],
            "model_routes": {"extract": ["cloud"], "score": ["cloud"]},
        }
    )
    store = InMemoryKeyStore()
    store.set("api-key-cloud", "K")
    # Every call "uses" 1,000,000 tokens = $1; the essential ceiling is 2x the budget = $1.
    report = run_nightly(engine, store, paid, handler(tokens=1_000_000), NOW)
    assert any("budget" in p for p in report.problems) and report.scored == 0
    assert report.new_jobs == 2 and "index.html" in (report.shortlist_path or "")


def test_hub_nightly_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from careerhub.cli import main

    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))
    assert main(["nightly"], InMemoryKeyStore()) == 0
    out = capsys.readouterr().out
    assert "Sources: 0 fetched, 0 failed" in out and "Shortlist:" in out
    assert (tmp_path / "shortlist" / "index.html").exists()


def test_tailoring_notes_are_written_for_the_shortlist_when_a_route_and_resume_exist(
    env: Any,
) -> None:
    engine, data = env
    (data / "inputs" / "resume").mkdir(parents=True)
    (data / "inputs" / "resume" / "resume.tex").write_text(
        r"\documentclass{article}\begin{document}Built ML tools.\end{document}"
    )
    settings = Settings.model_validate(
        {
            "providers": [{"name": "local", "kind": "local", "model": "m"}],
            "model_routes": {"extract": ["local"], "score": ["local"], "tailor": ["local"]},
        }
    )
    report = run_nightly(engine, InMemoryKeyStore(), settings, handler(), NOW)
    assert (report.scored, report.tailored, report.problems) == (1, 1, [])
    assert "Your ML project" in (data / "shortlist" / "index.html").read_text(encoding="utf-8")


def test_tailoring_is_silently_off_without_a_route_and_noted_without_a_resume(env: Any) -> None:
    engine, data = env
    quiet = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert quiet.tailored == 0 and not any("tailor" in p for p in quiet.problems)
    settings = Settings.model_validate(
        {
            "providers": [{"name": "local", "kind": "local", "model": "m"}],
            "model_routes": {"extract": ["local"], "score": ["local"], "tailor": ["local"]},
        }
    )
    report = run_nightly(engine, InMemoryKeyStore(), settings, handler(), NOW)
    assert any("no LaTeX resume" in p for p in report.problems)


def test_the_nightly_run_prunes_expired_saved_answers_and_reports_the_count(env: Any) -> None:
    engine, _ = env
    old = datetime(2025, 1, 1, tzinfo=UTC)
    with session_factory(engine)() as session:
        session.add(
            LLMCacheEntry(
                provider="p", model="m", prompt_hash="h", response_text="x", last_used_at=old
            )
        )
        session.commit()
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    assert report.cache_pruned == 1
    assert "Removed 1 expired saved answers." in report.summary()
