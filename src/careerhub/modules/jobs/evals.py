"""Measure how accurate extraction is, against hand-checked postings (PRD, "Evaluation").

An eval file lists cases: a posting and the right answer for level, locations, visa sponsorship,
and deadline (format documented in `evals/extraction/sample.yaml`). `run_extraction_eval` runs the
real extraction code over them and reports accuracy per field plus every miss, so a change to the
prompt or model can be judged by numbers. A case the model fails to answer validly counts as wrong
on every field.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from careerhub.core.models import Job
from careerhub.llm import Router
from careerhub.llm.validated import ValidationFailed
from careerhub.modules.jobs.extract import JobFields, Level, Visa, extract_fields

#: The fields measured, in report order.
FIELDS = ("level", "locations", "visa_sponsorship", "deadline")


class EvalError(Exception):
    """The eval file is missing or malformed."""


class Expected(BaseModel):
    """The hand-checked answers for one posting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: Level
    locations: list[str]
    visa_sponsorship: Visa
    deadline: date | None

    @field_validator("visa_sponsorship", mode="before")
    @classmethod
    def _yaml_booleans(cls, value: object) -> object:
        """Read unquoted `yes`/`no` (which YAML turns into True/False) as the words."""
        return {True: "yes", False: "no"}.get(value, value)  # type: ignore[call-overload]


class EvalCase(BaseModel):
    """One posting and its right answers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    company: str
    title: str
    location: str | None = None
    text: str
    expected: Expected


class _EvalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvalCase]


@dataclass
class Miss:
    """One wrong field on one case."""

    case_id: str
    field: str
    expected: object
    got: object


@dataclass
class EvalReport:
    """Accuracy per field, with the misses that explain it."""

    total: int = 0
    correct: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    misses: list[Miss] = field(default_factory=list)

    def accuracy(self, name: str) -> float:
        """Return the fraction of cases where `name` was right (0.0 with no cases)."""
        return self.correct[name] / self.total if self.total else 0.0

    def summary(self) -> str:
        """Render the report as plain text."""
        lines = [f"Extraction eval: {self.total} cases"]
        lines += [f"  {name}: {self.accuracy(name):.0%}" for name in FIELDS]
        lines += [
            f"  miss: {m.case_id} {m.field}: expected {m.expected!r}, got {m.got!r}"
            for m in self.misses
        ]
        return "\n".join(lines)


def load_cases(path: Path) -> list[EvalCase]:
    """Read an eval file. Raises `EvalError` if it is missing or doesn't match the format."""
    if not path.exists():
        raise EvalError(f"{path} does not exist.")
    try:
        return _EvalFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).cases
    except (yaml.YAMLError, ValidationError) as error:
        raise EvalError(f"{path.name} is not a valid eval file ({type(error).__name__}).") from None


def _norm(places: Sequence[str]) -> set[str]:
    return {" ".join(place.lower().split()) for place in places}


def _matches(name: str, expected: Expected, got: JobFields) -> bool:
    if name == "locations":
        return _norm(expected.locations) == _norm(got.locations)
    return bool(getattr(expected, name) == getattr(got, name))


def run_extraction_eval(
    router: Router, cases: Sequence[EvalCase], routes: Sequence[str]
) -> EvalReport:
    """Extract every case with the real extraction code and score it against the answers."""
    report = EvalReport(total=len(cases))
    for case in cases:
        job = Job(title=case.title, location=case.location, raw_text=case.text)
        try:
            got: JobFields | None = extract_fields(router, job, routes, company=case.company)
        except ValidationFailed:
            got = None
        for name in FIELDS:
            if got is not None and _matches(name, case.expected, got):
                report.correct[name] += 1
            else:
                report.misses.append(
                    Miss(
                        case.id,
                        name,
                        getattr(case.expected, name),
                        getattr(got, name) if got is not None else "no valid answer",
                    )
                )
    return report
