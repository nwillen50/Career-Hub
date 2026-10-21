from __future__ import annotations

import json
from pathlib import Path

import pytest

from careerhub.llm import LLMRequest, LLMResponse, Router
from careerhub.llm.provider import Destination
from careerhub.modules.jobs.evals import EvalError
from careerhub.modules.jobs.rank_evals import load_labeled, run_ranking_eval
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import Criterion, Rubric

SAMPLE = Path(__file__).parents[3] / "evals" / "ranking" / "sample.yaml"
RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
DOC = ProfileDoc(name="Jane Sample", skills=["Python"])


class Scripted:
    """A pretend model: extraction gets neutral fields; scoring rates by the job title."""

    name = "m"
    model = "m-1"
    destination = Destination.LOCAL
    trains_on_data = False

    def __init__(self, by_title: dict[str, int]) -> None:
        self.by_title = by_title

    def complete(self, request: LLMRequest) -> LLMResponse:
        if "extract structured facts" in request.prompt:
            text = json.dumps({"level": "unknown"})
        else:
            title = next(t for t in self.by_title if f"Title: {t}" in request.prompt)
            text = json.dumps(
                {
                    "criteria": [{"name": "field", "score": self.by_title[title], "reason": "r"}],
                    "explanation": "ok",
                }
            )
        return LLMResponse(text=text, provider=self.name, model=self.model)


GOOD_MODEL = {
    "Machine Learning Engineer, New Grad": 9,
    "Data Analyst": 6,
    "Senior Sales Director": 1,
}


def run(by_title: dict[str, int], constraints: HardConstraints | None = None):  # type: ignore[no-untyped-def]
    return run_ranking_eval(
        Router([Scripted(by_title)]),
        load_labeled(SAMPLE),
        DOC,
        RUBRIC,
        constraints or HardConstraints(),
        ["m"],
        ["m"],
    )


def test_the_sample_file_loads() -> None:
    assert [c.label for c in load_labeled(SAMPLE)] == ["great", "okay", "no"]


def test_a_good_ranking_passes_with_perfect_pair_order() -> None:
    report = run(GOOD_MODEL)
    assert report.passed and report.pair_accuracy == 1.0
    assert report.mean_score["great"] > report.mean_score["okay"] > report.mean_score["no"]
    assert "PASS" in report.summary()


def test_a_great_posting_filtered_out_fails_the_eval() -> None:
    report = run(GOOD_MODEL, HardConstraints(excluded_title_words=["engineer"]))
    assert report.great_filtered_out == ["rank-1"] and not report.passed
    assert "FAIL" in report.summary()


def test_too_many_nos_in_the_top_fail_the_eval() -> None:
    # Only one "no" exists in the sample, so one in the top is still allowed:
    assert run({**GOOD_MODEL, "Senior Sales Director": 10}).passed
    # Add a second "no" by relabeling via a copy of the cases.
    cases = [c.model_copy(update={"label": "no"}) for c in load_labeled(SAMPLE)]
    report = run_ranking_eval(
        Router([Scripted(GOOD_MODEL)]), cases, DOC, RUBRIC, HardConstraints(), ["m"], ["m"]
    )
    assert len(report.no_in_top) == 3 and not report.passed


def test_a_wrong_order_lowers_pair_accuracy() -> None:
    report = run(
        {**GOOD_MODEL, "Machine Learning Engineer, New Grad": 1, "Senior Sales Director": 9}
    )
    assert report.pair_accuracy is not None and report.pair_accuracy < 1.0


def test_unscorable_postings_are_listed_not_hidden() -> None:
    class Broken(Scripted):
        def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(text="nonsense", provider=self.name, model=self.model)

    report = run_ranking_eval(
        Router([Broken({})]), load_labeled(SAMPLE), DOC, RUBRIC, HardConstraints(), ["m"], ["m"]
    )
    assert len(report.unscored) == 3 and "could not be scored" in report.summary()


def test_bad_files_are_reported(tmp_path: Path) -> None:
    with pytest.raises(EvalError):
        load_labeled(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("cases:\n  - id: x\n    label: maybe\n", encoding="utf-8")
    with pytest.raises(EvalError):
        load_labeled(bad)


def test_an_unquoted_no_label_is_read_as_the_word(tmp_path: Path) -> None:
    """YAML turns a bare `no` into False; the loader must still read it as the label."""
    path = tmp_path / "labels.yaml"
    path.write_text(
        "cases:\n  - {id: a, company: C, title: T, text: x, label: no, reason: r}\n",
        encoding="utf-8",
    )
    assert load_labeled(path)[0].label == "no"


def test_an_unquoted_visa_answer_in_an_extraction_eval_is_read_as_the_word(tmp_path: Path) -> None:
    from careerhub.modules.jobs.evals import load_cases

    path = tmp_path / "extract.yaml"
    path.write_text(
        "cases:\n  - id: a\n    company: C\n    title: T\n    text: x\n    expected:\n"
        "      level: unknown\n      locations: []\n      visa_sponsorship: no\n"
        "      deadline: null\n",
        encoding="utf-8",
    )
    assert load_cases(path)[0].expected.visa_sponsorship == "no"
