"""Ranking evals: does the pipeline put the user's labeled postings in a sensible order.

Each labeled posting (great, okay, no) runs through extraction, the hard filters, and scoring, in a
scratch in-memory database that never touches the user's real one. Measures (PRD, "Evaluation"):

- **Filter eval:** no posting labeled `great` is filtered out. Any drop is a failure.
- **Top-10 eval:** at most one `no` among the 10 best-scored postings (filtered-out postings rank
  last).
- **Ordering:** the share of label pairs (great over okay, great over no, okay over no) where the
  better label got the higher score, and the average score for each label.

`RankingReport.passed` is the PRD's acceptance rule: no great filtered out, and at most one `no`
in the top 10.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Base, Job, Source
from careerhub.llm import Router
from careerhub.llm.validated import ValidationFailed
from careerhub.modules.jobs.evals import EvalError
from careerhub.modules.jobs.extract import extract_and_save
from careerhub.modules.jobs.filters import assess_filters
from careerhub.modules.jobs.scoring import ScoringError, score_job
from careerhub.modules.jobs.store import NormalizedJob, store_jobs
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import Rubric

Label = Literal["great", "okay", "no"]
_RANK = {"great": 2, "okay": 1, "no": 0}
TOP_N = 10


class LabeledPosting(BaseModel):
    """A posting and the user's judgment of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    company: str
    title: str
    location: str | None = None
    text: str
    label: Label
    reason: str

    @field_validator("label", mode="before")
    @classmethod
    def _yaml_booleans(cls, value: object) -> object:
        """Read an unquoted `no` (which YAML turns into False) as the word "no"."""
        return "no" if value is False else value


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[LabeledPosting]


@dataclass
class RankingReport:
    """Results of one ranking eval run."""

    total: int = 0
    great_filtered_out: list[str] = field(default_factory=list)
    no_in_top: list[str] = field(default_factory=list)
    unscored: list[str] = field(default_factory=list)
    pair_accuracy: float | None = None
    mean_score: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """The PRD's rule: no `great` filtered out and at most one `no` in the top 10."""
        return not self.great_filtered_out and len(self.no_in_top) <= 1

    def summary(self) -> str:
        """Render the report as plain text."""
        pairs = "n/a" if self.pair_accuracy is None else f"{self.pair_accuracy:.0%}"
        lines = [
            f"Ranking eval: {self.total} postings, {'PASS' if self.passed else 'FAIL'}",
            f"  great postings filtered out: {len(self.great_filtered_out)}"
            f" {self.great_filtered_out or ''}".rstrip(),
            f"  'no' postings in the top {TOP_N}: {len(self.no_in_top)}"
            f" {self.no_in_top or ''}".rstrip(),
            f"  label pairs in the right order: {pairs}",
        ]
        lines += [
            f"  mean score for {name}: {score:.1f}" for name, score in self.mean_score.items()
        ]
        if self.unscored:
            lines.append(f"  could not be scored: {self.unscored}")
        return "\n".join(lines)


def load_labeled(path: Path) -> list[LabeledPosting]:
    """Read a ranking-eval file. Raises `EvalError` if it is missing or malformed."""
    if not path.exists():
        raise EvalError(f"{path} does not exist.")
    try:
        return _File.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).cases
    except (yaml.YAMLError, ValidationError) as error:
        raise EvalError(
            f"{path.name} is not a valid ranking eval file ({type(error).__name__})."
        ) from None


def run_ranking_eval(
    router: Router,
    cases: Sequence[LabeledPosting],
    doc: ProfileDoc,
    rubric: Rubric,
    constraints: HardConstraints,
    extract_routes: Sequence[str],
    score_routes: Sequence[str],
) -> RankingReport:
    """Run every labeled posting through extraction, filters, and scoring, then measure."""
    engine = create_engine("sqlite://")  # in memory: this data never reaches the user's database
    Base.metadata.create_all(engine)
    report = RankingReport(total=len(cases))
    with Session(engine) as session:
        source = Source(kind="eval", name="eval", url="eval://ranking", status="approved")
        session.add(source)
        session.commit()
        store_jobs(
            session,
            source,
            [
                NormalizedJob(c.company, c.id, c.title, f"eval://{c.id}", c.location, c.text)
                for c in cases
            ],
        )
        scores: dict[str, float | None] = {}
        for case in cases:
            job = session.scalars(select(Job).where(Job.external_id == case.id)).one()
            scores[case.id] = _run_one(
                session, router, job, doc, rubric, constraints, extract_routes, score_routes
            )
            assessment = session.scalars(
                select(Assessment).where(Assessment.job_id == job.id)
            ).one()
            if case.label == "great" and not assessment.filter_passed:
                report.great_filtered_out.append(case.id)
            if assessment.filter_passed and scores[case.id] is None:
                report.unscored.append(case.id)
    _measure(report, cases, scores)
    return report


def _run_one(
    session: Session,
    router: Router,
    job: Job,
    doc: ProfileDoc,
    rubric: Rubric,
    constraints: HardConstraints,
    extract_routes: Sequence[str],
    score_routes: Sequence[str],
) -> float | None:
    """Run one job through the pipeline; return its score, or None if filtered or unscorable."""
    try:
        extract_and_save(session, router, job, extract_routes)
    except ValidationFailed:
        pass  # filters and scoring still work without extracted fields
    assessment = assess_filters(session, job, constraints)
    if not assessment.filter_passed:
        return None
    try:
        score_job(session, router, job, doc, rubric, score_routes)
    except (ValidationFailed, ScoringError):
        return None
    return assessment.fit_score


def _measure(
    report: RankingReport, cases: Sequence[LabeledPosting], scores: dict[str, float | None]
) -> None:
    """Fill in the top-10, pair-ordering, and mean-score measures."""
    ranked = sorted(cases, key=lambda c: (scores[c.id] is None, -(scores[c.id] or 0.0)))
    top = [c for c in ranked[:TOP_N] if scores[c.id] is not None]
    report.no_in_top = [c.id for c in top if c.label == "no"]
    right = total = 0
    for a, b in combinations(cases, 2):
        if a.label == b.label:
            continue
        better, worse = (a, b) if _RANK[a.label] > _RANK[b.label] else (b, a)
        total += 1
        sb, sw = scores[better.id], scores[worse.id]
        right += (sb or 0.0) > (sw or 0.0) if sb is not None else 0
    report.pair_accuracy = right / total if total else None
    for label in ("great", "okay", "no"):
        values = [s for c in cases if c.label == label and (s := scores[c.id]) is not None]
        if values:
            report.mean_score[label] = sum(values) / len(values)
