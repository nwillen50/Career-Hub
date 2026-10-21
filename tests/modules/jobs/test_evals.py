from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from careerhub.llm import Router
from careerhub.llm.fake import FakeProvider
from careerhub.modules.jobs.evals import (
    EvalCase,
    EvalError,
    load_cases,
    run_extraction_eval,
)

SAMPLE = Path(__file__).parents[3] / "evals" / "extraction" / "sample.yaml"


def answers(cases: list[EvalCase], overrides: dict[int, dict[str, Any]] | None = None) -> list[str]:
    """Perfect model answers for `cases`, with per-case-index overrides."""
    overrides = overrides or {}
    out = []
    for i, case in enumerate(cases):
        fields = json.loads(case.expected.model_dump_json())
        fields.update(overrides.get(i, {}))
        out.append(json.dumps(fields))
    return out


def test_the_sample_file_loads() -> None:
    cases = load_cases(SAMPLE)
    assert [c.id for c in cases] == ["sample-1", "sample-2", "sample-3"]


def test_a_perfect_model_scores_100_percent() -> None:
    cases = load_cases(SAMPLE)
    router = Router([FakeProvider("m", replies=answers(cases))])
    report = run_extraction_eval(router, cases, ["m"])
    assert report.total == 3 and report.misses == []
    assert all(
        report.accuracy(name) == 1.0
        for name in ("level", "locations", "visa_sponsorship", "deadline")
    )


def test_wrong_fields_are_counted_and_listed() -> None:
    cases = load_cases(SAMPLE)
    replies = answers(cases, {1: {"level": "mid"}, 2: {"visa_sponsorship": "no"}})
    report = run_extraction_eval(Router([FakeProvider("m", replies=replies)]), cases, ["m"])
    assert report.accuracy("level") == pytest.approx(2 / 3)
    assert report.accuracy("visa_sponsorship") == pytest.approx(2 / 3)
    assert report.accuracy("deadline") == 1.0
    assert {(m.case_id, m.field) for m in report.misses} == {
        ("sample-2", "level"),
        ("sample-3", "visa_sponsorship"),
    }
    assert "sample-2 level" in report.summary() and "67%" in report.summary()


def test_location_order_and_case_do_not_matter() -> None:
    cases = load_cases(SAMPLE)
    replies = answers(cases, {2: {"locations": ["austin, tx", "NEW YORK, NY"]}})
    report = run_extraction_eval(Router([FakeProvider("m", replies=replies)]), cases, ["m"])
    assert report.accuracy("locations") == 1.0


def test_an_unanswerable_case_is_wrong_on_every_field() -> None:
    cases = load_cases(SAMPLE)[:1]
    report = run_extraction_eval(Router([FakeProvider("m", replies=["nonsense"])]), cases, ["m"])
    assert all(report.accuracy(name) == 0.0 for name in ("level", "locations", "deadline"))
    assert len(report.misses) == 4 and report.misses[0].got == "no valid answer"


def test_bad_eval_files_are_reported(tmp_path: Path) -> None:
    with pytest.raises(EvalError):
        load_cases(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("cases:\n  - id: x\n", encoding="utf-8")
    with pytest.raises(EvalError):
        load_cases(bad)
    bad.write_text("cases: [unclosed\n", encoding="utf-8")
    with pytest.raises(EvalError):
        load_cases(bad)
