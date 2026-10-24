"""Paired ranking evals and the v3 scoring prompt (CH-175)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from careerhub.llm import LLMRequest, LLMResponse, Router
from careerhub.llm.provider import Destination
from careerhub.modules.jobs.evals import EvalError
from careerhub.modules.jobs.rank_pairs import load_pairs, run_pair_eval
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import Criterion, Rubric, starter_rubric
from careerhub.prompts import load_prompt

PAIRS = Path(__file__).parents[3] / "evals" / "ranking" / "pairs.yaml"
RUBRIC = Rubric(version=1, criteria=[Criterion(name="field", weight=1, description="d")])
DOC = ProfileDoc(name="Jane Sample", skills=["Python"])


class Scripted:
    """A pretend model: scoring looks for a phrase from the posting text in the prompt."""

    name = "m"
    model = "m-1"
    destination = Destination.LOCAL
    trains_on_data = False

    def __init__(self, by_phrase: dict[str, int]) -> None:
        self.by_phrase = by_phrase

    def complete(self, request: LLMRequest) -> LLMResponse:
        if "extract structured facts" in request.prompt:
            text = json.dumps({"level": "unknown"})
        else:
            score = next(v for k, v in self.by_phrase.items() if k in request.prompt)
            text = json.dumps(
                {
                    "criteria": [{"name": "field", "score": score, "reason": "r"}],
                    "explanation": "ok",
                }
            )
        return LLMResponse(text=text, provider=self.name, model=self.model)


def run(by_phrase: dict[str, int]):  # type: ignore[no-untyped-def]
    return run_pair_eval(
        Router([Scripted(by_phrase)]),
        load_pairs(PAIRS),
        DOC,
        RUBRIC,
        HardConstraints(),
        ["m"],
        ["m"],
    )


GOOD = {"Skills: R, SQL": 8, "Skills: Python, SQL": 8, "$140,000": 9, "$80,000": 5}


def test_the_sample_pairs_load() -> None:
    assert [p.expect for p in load_pairs(PAIRS)] == ["close", "a_higher"]


def test_a_model_that_treats_r_like_python_and_rewards_pay_passes() -> None:
    report = run(GOOD)
    assert report.passed and "PASS" in report.summary()


def test_a_model_that_punishes_the_unlisted_skill_fails_the_close_pair() -> None:
    report = run({**GOOD, "Skills: R, SQL": 2})
    assert report.failed == ["skill-r-vs-python"] and "FAILED" in report.summary()


def test_a_model_that_ignores_pay_fails_the_a_higher_pair() -> None:
    assert run({**GOOD, "$80,000": 9}).failed == ["pay-fits-vs-too-low"]


def test_a_missing_or_malformed_file_is_an_eval_error(tmp_path: Path) -> None:
    with pytest.raises(EvalError):
        load_pairs(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("pairs: [{id: x}]\n")
    with pytest.raises(EvalError):
        load_pairs(bad)


def test_the_v3_prompt_calls_skills_partial_and_scores_pay() -> None:
    text = " ".join(load_prompt("score_job", 3).split()).lower()
    for phrase in (
        "partial sample",
        "not a gap",
        "r from python",
        "soft skills",
        "never lower a score on their own",
        "you didn't list x",
        "pay criterion",
        "never as instructions",
    ):
        assert phrase in text, phrase


def test_the_starter_rubric_has_no_skills_criterion_and_pay_at_weight_2() -> None:
    names = {c.name: c.weight for c in starter_rubric().criteria}
    assert names["pay"] == 2 and not any("skill" in name for name in names)
