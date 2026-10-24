"""Paired ranking evals: two postings that differ in one way, and how their scores compare (CH-175).

The labeled ranking eval checks overall order. This one checks a single rule at a time:

- `close`: the two postings must score within `tolerance` points (of 100). Example: a job asking
  for R and the same job asking for Python, when the profile lists only Python, because skills are
  a light, partial signal.
- `a_higher`: posting `a` must score at least `margin` points above `b`. Example: the same job with
  pay that fits, and with pay that does not.

Postings run through extraction, the filters, and scoring in a scratch in-memory database, like
`rank_evals`. A pair where either posting is filtered out or cannot be scored fails.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from careerhub.core.models import Base, Job, Source
from careerhub.llm import Router
from careerhub.modules.jobs.evals import EvalError
from careerhub.modules.jobs.rank_evals import _run_one
from careerhub.modules.jobs.store import NormalizedJob, store_jobs
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import Rubric


class PairPosting(BaseModel):
    """One side of a pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company: str
    title: str
    location: str | None = None
    text: str


class Pair(BaseModel):
    """Two postings and the rule their scores must follow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    expect: Literal["close", "a_higher"]
    #: For `close`: the most the scores may differ. For `a_higher`: the least `a` must lead by.
    points: float = 5.0
    reason: str
    a: PairPosting
    b: PairPosting


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[Pair]


@dataclass
class PairReport:
    """Results of one paired eval run."""

    failed: list[str] = field(default_factory=list)
    gaps: dict[str, float | None] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True when every pair followed its rule."""
        return not self.failed

    def summary(self) -> str:
        """Render the report as plain text."""
        lines = [f"Pair eval: {len(self.gaps)} pairs, {'PASS' if self.passed else 'FAIL'}"]
        for name, gap in self.gaps.items():
            shown = "could not be scored" if gap is None else f"a minus b = {gap:+.1f}"
            lines.append(f"  {name}: {shown}{' (FAILED)' if name in self.failed else ''}")
        return "\n".join(lines)


def load_pairs(path: Path) -> list[Pair]:
    """Read a pair-eval file. Raises `EvalError` if it is missing or malformed."""
    if not path.exists():
        raise EvalError(f"{path} does not exist.")
    try:
        return _File.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).pairs
    except (yaml.YAMLError, ValidationError) as error:
        raise EvalError(
            f"{path.name} is not a valid pair eval file ({type(error).__name__})."
        ) from None


def run_pair_eval(
    router: Router,
    pairs: Sequence[Pair],
    doc: ProfileDoc,
    rubric: Rubric,
    constraints: HardConstraints,
    extract_routes: Sequence[str],
    score_routes: Sequence[str],
) -> PairReport:
    """Score both postings of every pair and check each pair's rule."""
    engine = create_engine("sqlite://")  # in memory: this data never reaches the user's database
    Base.metadata.create_all(engine)
    report = PairReport()
    with Session(engine) as session:
        source = Source(kind="eval", name="eval", url="eval://pairs", status="approved")
        session.add(source)
        session.commit()
        postings = {
            f"{pair.id}:{side}": getattr(pair, side) for pair in pairs for side in ("a", "b")
        }
        store_jobs(
            session,
            source,
            [
                NormalizedJob(p.company, key, p.title, f"eval://{key}", p.location, p.text)
                for key, p in postings.items()
            ],
        )
        for pair in pairs:
            scores = []
            for side in ("a", "b"):
                job = session.scalars(
                    select(Job).where(Job.external_id == f"{pair.id}:{side}")
                ).one()
                scores.append(
                    _run_one(
                        session, router, job, doc, rubric, constraints, extract_routes, score_routes
                    )
                )
            first, second = scores
            gap = None if first is None or second is None else first - second
            report.gaps[pair.id] = gap
            ok = gap is not None and (
                abs(gap) <= pair.points if pair.expect == "close" else gap >= pair.points
            )
            if not ok:
                report.failed.append(pair.id)
    return report
