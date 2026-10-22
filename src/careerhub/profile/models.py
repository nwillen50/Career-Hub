"""What the profile files contain, as validated models.

Both models reject unknown keys, so a typo like `locatoins:` is an error instead of a silently
ignored constraint. All of this is Personal data (PRD, privacy tiers).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

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
    #: Nicknames and handles (for example "Nick", "nwillen50") removed from text sent to models.
    aliases: list[str] = Field(default_factory=list)
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
    #: A fully remote job passes from anywhere.
    remote_ok: bool = True
    #: A hybrid job passes only if it is in one of your locations (you'd go in some days).
    hybrid_ok: bool = True
    #: An onsite job passes only if it is in one of your locations.
    onsite_ok: bool = True
    min_salary_usd: int | None = Field(default=None, ge=0)
    excluded_companies: list[str] = Field(default_factory=list)
    #: Words that rule a job out when they appear in its title, e.g. ["intern", "senior"].
    excluded_title_words: list[str] = Field(default_factory=list)
    #: Other names for your places: {"New York, NY": ["the city", "Midtown"]}. Added to the
    #: built-in list (NYC, Manhattan, SF, Bay Area...). A name matches only as whole words.
    location_aliases: dict[str, list[str]] = Field(default_factory=dict)


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
    #: Fields of study the user counts as their own, beyond their degree (for example a physics
    #: major listing "electrical engineering"). Degree field is never a filter; it helps scoring.
    also_qualified_fields: list[str] = Field(default_factory=list)
    #: Points (on a 0 to 10 scale) an internship ranks below a full-time job. Ordering only; the
    #: stored score is never changed. 0 means no penalty.
    internship_rank_penalty: float = Field(default=0.0, ge=0, le=10)
    #: Your work authorization, if you want citizenship-required jobs checked. Unset: they are
    #: only marked uncertain.
    work_authorization: Literal["us_citizen", "work_authorized", "needs_sponsorship"] | None = None
