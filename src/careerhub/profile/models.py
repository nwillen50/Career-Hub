"""What the profile files contain, as validated models.

Both models reject unknown keys, so a typo like `locatoins:` is an error instead of a silently
ignored constraint. All of this is Personal data (PRD, privacy tiers).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Base for the profile models: unknown keys are errors and objects are read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Education(_Strict):
    """One school the user attended or attends."""

    school: str
    degree: str
    #: Month of graduation (or expected graduation) as a date, e.g. 2027-05-01.
    graduation: date | None = None


class ProfileDoc(_Strict):
    """The contents of `profile.md`: front matter for fields, the body for free text."""

    name: str
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    #: Interests the user states themselves (inferred ones are added later, with a proposal).
    interests: list[str] = Field(default_factory=list)
    #: The Markdown after the front matter: anything the user wants Career Hub to know.
    about: str = ""


class HardConstraints(_Strict):
    """Rules a job must satisfy; jobs that break one are filtered out before scoring."""

    #: Places the user will work, e.g. ["New York, NY", "Boston, MA"]. Empty means anywhere.
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    min_salary_usd: int | None = Field(default=None, ge=0)
    excluded_companies: list[str] = Field(default_factory=list)
    #: Words that rule a job out when they appear in its title, e.g. ["intern", "senior"].
    excluded_title_words: list[str] = Field(default_factory=list)


class Timeline(_Strict):
    """When the user wants to start work."""

    earliest_start: date | None = None
    latest_start: date | None = None


class Preferences(_Strict):
    """The contents of `preferences.yaml`."""

    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    timeline: Timeline = Field(default_factory=Timeline)
    #: Hours per week the user can put into the job search (feeds the weekly plan).
    weekly_time_budget_hours: float = Field(default=5.0, gt=0, le=80)
