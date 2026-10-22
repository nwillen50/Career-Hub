"""Measure how well documents are read into facts, against made-up cases (CH-110).

`evals/profile/sample.yaml` lists synthetic resumes and transcripts with the facts a good reading
must find. `run_profile_eval` runs the real extraction prompt and schema over them and reports
accuracy per field (the share of expected items found), plus every miss, so a change to the prompt
or model can be judged by numbers. A case the model can't answer validly scores zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from careerhub.llm import Router
from careerhub.llm.validated import ValidationFailed, complete_validated
from careerhub.profile.extract import Kind, ProfileFacts, build_request
from careerhub.profile.models import ProfileDoc

FIELDS = ("gpa", "skills", "schools", "organizations", "course_codes")


class ProfileEvalError(Exception):
    """The eval file is missing or malformed."""


class Expected(BaseModel):
    """The facts a good reading finds in one document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gpa: float | None
    skills: list[str]
    schools: list[str]
    organizations: list[str]
    course_codes: list[str]


class Case(BaseModel):
    """One made-up document and its right answers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Kind
    text: str
    expected: Expected


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[Case]


@dataclass
class ProfileEvalReport:
    """Accuracy per field (0 to 1) and the items that were missed."""

    accuracy: dict[str, float]
    misses: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Render the report as plain text."""
        lines = [f"{name}: {self.accuracy[name]:.0%}" for name in FIELDS]
        return "\n".join(lines + [f"Missed: {m}" for m in self.misses])


def load_cases(path: Path) -> list[Case]:
    """Read and validate an eval file."""
    try:
        return _File.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).cases
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ProfileEvalError(
            f"Can't read eval file {path.name}: {type(error).__name__}"
        ) from None


def _found(expected: Sequence[str], got: Sequence[str | None]) -> tuple[int, list[str]]:
    """Count expected items present in `got` (ignoring case); return the count and the misses."""
    have = {g.strip().lower() for g in got if g}
    missed = [e for e in expected if e.strip().lower() not in have]
    return len(expected) - len(missed), missed


def run_profile_eval(
    router: Router, cases: Sequence[Case], routes: Sequence[str]
) -> ProfileEvalReport:
    """Run extraction over `cases` and score each field."""
    hits = dict.fromkeys(FIELDS, 0)
    totals = dict.fromkeys(FIELDS, 0)
    misses: list[str] = []
    for case in cases:
        try:
            facts = complete_validated(
                router,
                build_request(case.kind, case.text, ProfileDoc(name="")),
                ProfileFacts,
                routes,
            )
        except ValidationFailed:
            facts = ProfileFacts()
        lists: dict[str, tuple[Sequence[str], Sequence[str | None]]] = {
            "skills": (case.expected.skills, facts.skills),
            "schools": (case.expected.schools, [e.school for e in facts.education]),
            "organizations": (
                case.expected.organizations,
                [e.organization for e in facts.experiences],
            ),
            "course_codes": (case.expected.course_codes, [c.code for c in facts.courses]),
        }
        for name, (expected, got) in lists.items():
            found, missed = _found(expected, got)
            hits[name] += found
            totals[name] += len(expected)
            misses += [f"{case.id} {name}: {item}" for item in missed]
        if case.expected.gpa is not None:
            totals["gpa"] += 1
            if facts.gpa == case.expected.gpa:
                hits["gpa"] += 1
            else:
                misses.append(f"{case.id} gpa")
    accuracy = {n: (hits[n] / totals[n] if totals[n] else 1.0) for n in FIELDS}
    return ProfileEvalReport(accuracy, misses)
