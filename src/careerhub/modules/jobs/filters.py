"""Hard filters: rule out jobs that break a constraint the user stated (build step 6).

Rules (all from `preferences.yaml`): excluded companies, excluded title words, allowed locations
(or remote), a minimum salary, and eligibility (degree level, graduation window, citizenship).
The design rule is **never drop a good job by mistake**:

- A job is rejected only when a fact we *have* breaks a rule. A fact we don't have (no location
  extracted, no salary listed) is recorded as `uncertain` and the job passes.
- Every rejection records its reason in plain English, so the user can see and dispute it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Job
from careerhub.core.names import company_key
from careerhub.modules.jobs.extract import JobFields
from careerhub.profile.eligibility import Eligibility, rank
from careerhub.profile.models import HardConstraints


@dataclass(frozen=True)
class FilterResult:
    """Whether a job passed, why not, and what couldn't be checked."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _city(place: str) -> str:
    """Return the city part of "Boston, MA" in lowercase."""
    return " ".join(place.split(",")[0].lower().split())


def _location_ok(
    constraints: HardConstraints, job_places: list[str], fields: JobFields | None
) -> tuple[bool | None, str]:
    """Return (True/False/None for unknown, why) for the location rules."""
    if not constraints.locations:
        return True, ""
    if constraints.remote_ok and fields is not None and fields.remote == "remote":
        return True, ""
    if not job_places:
        return None, "no location was found for this job"
    wanted = {_city(place) for place in constraints.locations}
    if any(
        city and city in " ".join(place.lower().split()) for place in job_places for city in wanted
    ):
        return True, ""
    return False, f"located in {', '.join(job_places)}, outside your locations"


def _eligibility(
    fields: JobFields | None, me: Eligibility, reasons: list[str], uncertain: list[str]
) -> None:
    """Add the eligibility rules (CH-113): degree level, graduation window, work authorization.

    These are the only eligibility rules. Degree field and job level (new grad, entry, intern) are
    never filters. A fact we lack makes the job `uncertain`, not rejected.
    """
    if fields is None:
        return
    needed = fields.degree_required
    if rank(needed) > rank("none"):
        if me.highest_degree is None:
            uncertain.append(f"requires a {needed} degree; your degree level isn't known")
        elif rank(needed) > rank(me.highest_degree):
            if fields.equivalent_experience_ok == "yes":
                uncertain.append(f"requires a {needed} degree (equivalent experience is accepted)")
            else:
                reasons.append(f"requires a {needed} degree; your highest is {me.highest_degree}")
    low, high = fields.graduation_earliest, fields.graduation_latest
    if low or high:
        if me.graduation is None:
            uncertain.append("targets a graduation window; your graduation date isn't known")
        elif (high and me.graduation > high) or (low and me.graduation < low):
            window = f"{low or 'any time'} to {high or 'any time'}"
            reasons.append(f"targets graduates between {window}; you graduate {me.graduation}")
    if fields.work_authorization == "clearance_required":
        uncertain.append("requires a security clearance")
    elif fields.work_authorization == "us_citizen_required":
        if me.work_authorization is None:
            uncertain.append("requires US citizenship; set work_authorization in preferences.yaml")
        elif me.work_authorization != "us_citizen":
            reasons.append(f"requires US citizenship; your setting is {me.work_authorization}")


def apply_filters(
    job: Job,
    fields: JobFields | None,
    constraints: HardConstraints,
    eligibility: Eligibility | None = None,
) -> FilterResult:
    """Check `job` against `constraints`. `fields` are the extracted facts, if any."""
    reasons: list[str] = []
    uncertain: list[str] = []
    excluded = {company_key(name) for name in constraints.excluded_companies}
    if company_key(job.company.name) in excluded:
        reasons.append(f"company {job.company.name} is on your excluded list")
    bad_words = _words(" ".join(constraints.excluded_title_words)) & _words(job.title)
    if bad_words:
        reasons.append(f"title contains excluded word(s): {', '.join(sorted(bad_words))}")
    places = (
        list(fields.locations)
        if fields and fields.locations
        else ([job.location] if job.location else [])
    )
    ok, why = _location_ok(constraints, places, fields)
    if ok is False:
        reasons.append(why)
    elif ok is None:
        uncertain.append(why)
    if constraints.min_salary_usd is not None:
        top = fields.salary_max_usd if fields else None
        if top is None:
            uncertain.append("no salary was listed")
        elif top < constraints.min_salary_usd:
            reasons.append(f"pay tops out at ${top:,}, below your ${constraints.min_salary_usd:,}")
    _eligibility(fields, eligibility or Eligibility(), reasons, uncertain)
    return FilterResult(not reasons, reasons, uncertain)


def assess_filters(
    session: Session,
    job: Job,
    constraints: HardConstraints,
    eligibility: Eligibility | None = None,
) -> Assessment:
    """Run the filters on `job` and save the result (replacing any earlier one)."""
    saved = job.structured_fields.get("fields")
    fields = JobFields.model_validate(saved) if saved else None
    result = apply_filters(job, fields, constraints, eligibility)
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    if assessment is None:
        assessment = Assessment(job_id=job.id)
        session.add(assessment)
    assessment.filter_passed = result.passed
    assessment.filter_results = {"reasons": result.reasons, "uncertain": result.uncertain}
    session.commit()
    return assessment
