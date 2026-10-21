"""The scoring rubric: a versioned list of criteria the scorer reads on every run (PRD).

Stored as `profile/rubric.yaml` in the data folder. Changes arrive as proposals the user accepts
(step 9) and bump `version`. This module only loads and validates it; scoring is step 6.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from careerhub.core.paths import data_dir
from careerhub.profile.files import PROFILE_DIRNAME, ProfileFileError, parse_yaml, validate_model

RUBRIC_FILENAME = "rubric.yaml"


class Criterion(BaseModel):
    """One thing a job is judged on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    #: How much this criterion counts relative to the others.
    weight: float = Field(gt=0, le=100)
    #: Plain English: what a great, okay, and poor match look like (shown to the scoring model).
    description: str = Field(min_length=1)


class Rubric(BaseModel):
    """A version of the rubric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def _names_are_unique(self) -> Rubric:
        """Reject two criteria with the same name."""
        names = [criterion.name.lower() for criterion in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError("criteria names must be unique")
        return self


def rubric_path() -> Path:
    """Return where `rubric.yaml` should be (it may not exist)."""
    return data_dir() / PROFILE_DIRNAME / RUBRIC_FILENAME


def starter_rubric() -> Rubric:
    """Return a reasonable first rubric (from the PRD's examples) for the user to edit."""
    return Rubric(
        version=1,
        criteria=[
            Criterion(name="field", weight=3, description="The work matches the fields I want."),
            Criterion(name="level", weight=3, description="The role suits an early-career hire."),
            Criterion(name="location", weight=2, description="Based where I can work."),
            Criterion(name="company stage", weight=1, description="Stage and size I'd thrive in."),
            Criterion(name="learning potential", weight=2, description="I'd grow quickly there."),
        ],
    )


def load_rubric(path: Path | None = None) -> Rubric | None:
    """Read `rubric.yaml`. Returns None if the file doesn't exist.

    Raises:
        ProfileFileError: the file exists but is invalid (field names only, never values).

    """
    path = path or rubric_path()
    if not path.exists():
        return None
    try:
        return validate_model(
            Rubric, parse_yaml(path.read_text(encoding="utf-8"), path.name), path.name
        )
    except ValueError as error:  # a custom validator's message (no user values in it)
        raise ProfileFileError(f"{path.name} is invalid: {error}") from None
