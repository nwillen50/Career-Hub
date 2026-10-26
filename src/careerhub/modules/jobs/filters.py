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
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Job
from careerhub.core.names import company_key
from careerhub.modules.jobs.extract import JobFields
from careerhub.modules.jobs.places import country_matches, parse_places
from careerhub.modules.jobs.visa import visa_flag
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


#: Endings that still mean the same word: "intern" matches "interns" and "internship", but
#: "internal" is a different word.
_SUFFIXES = ("s", "es", "ed", "ing", "ship", "ships")
#: Short forms and what they stand for (both directions are checked).
ABBREVIATIONS = {
    "sr": "senior",
    "jr": "junior",
    "mgr": "manager",
    "eng": "engineer",
    "swe": "software engineer",
}
#: Names for the same metro area. A wanted place in a group matches a job in any of its names.
BUILT_IN_PLACES = (
    ("new york", "nyc", "new york city", "manhattan", "brooklyn", "queens", "the bronx"),
    ("san francisco", "sf", "bay area", "south san francisco"),
    ("los angeles", "la"),
    ("washington", "dc", "washington dc"),
)


def _loose_forms(word: str) -> set[str]:
    """Return the other words that count as `word` (plurals, endings, abbreviations)."""
    forms = {word + suffix for suffix in _SUFFIXES}
    for short, long in ABBREVIATIONS.items():
        if word == long.split()[0] and " " not in long:
            forms.add(short)
        if word == short:
            forms.update(long.split() if " " not in long else {long.split()[0]})
    return forms


def _title_matches(excluded: list[str], title: str) -> tuple[list[str], list[str]]:
    """Return (exact hits, loose-hit explanations) of excluded words in `title`.

    An exact hit is the same whole word. A loose hit is a plural or ending of it, or an
    abbreviation ("Sr." for "senior"); it is reported with the words involved so it can be shown.
    """
    title_words = _words(title)
    exact = sorted(title_words & _words(" ".join(excluded)))
    loose = []
    for word in sorted(_words(" ".join(excluded)) - set(exact)):
        for found in sorted(title_words & _loose_forms(word)):
            loose.append(f"'{found}' in the title looks like your excluded word '{word}'")
    return exact, loose


def _aliases(city: str, extra: list[str]) -> set[str]:
    """Return the other names for `city`: the built-in metro groups plus the user's own."""
    names = {" ".join(name.lower().split()) for name in extra}
    for group in BUILT_IN_PLACES:
        if city in group:
            names.update(group)
    names.discard(city)
    return {name for name in names if name}


def _city(place: str) -> str:
    """Return the city part of "Boston, MA" in lowercase."""
    return " ".join(place.split(",")[0].lower().split())


def _mode_switched_off(constraints: HardConstraints, mode: str) -> str | None:
    """Return the reason if the job's work mode is one the user turned off, else None."""
    allowed = {
        "remote": constraints.remote_ok,
        "hybrid": constraints.hybrid_ok,
        "onsite": constraints.onsite_ok,
    }
    if mode in allowed and not allowed[mode]:
        return f"{mode}, and you turned {mode} off"
    return None


def _location_ok(
    constraints: HardConstraints, job_places: list[str], fields: JobFields | None
) -> tuple[bool | None, str]:
    """Return (True/False/None for unknown, why) for the location rules."""
    if not constraints.locations:
        return True, ""
    if constraints.remote_ok and fields is not None and fields.remote == "remote":
        return True, ""  # a remote job passes from anywhere
    if not job_places:
        return None, "no location was found for this job"
    places = [" ".join(place.lower().split()) for place in job_places]
    read_places = parse_places(job_places)
    for wanted in constraints.locations:
        if country_matches(
            wanted, read_places
        ):  # "Germany" matches "Berlin, Germany", "Munich, DE"
            return True, ""
        city = _city(wanted)
        if city and any(city in place for place in places):
            return True, ""
        names = _aliases(city, constraints.location_aliases.get(wanted, []))
        if any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", p) for name in names for p in places):
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
    aliases: Mapping[str, str] | None = None,
) -> FilterResult:
    """Check `job` against `constraints`. `fields` are the extracted facts, if any."""
    reasons: list[str] = []
    uncertain: list[str] = []
    excluded = {company_key(name, aliases) for name in constraints.excluded_companies}
    if company_key(job.company.name, aliases) in excluded:
        reasons.append(f"company {job.company.name} is on your excluded list")
    bad_words, loose_hits = _title_matches(constraints.excluded_title_words, job.title)
    if bad_words:
        reasons.append(f"title contains excluded word(s): {', '.join(bad_words)}")
    # A wrong hard filter hides a job silently, so a loose match only raises a flag.
    uncertain.extend(loose_hits)
    places = (
        list(fields.locations)
        if fields and fields.locations
        else ([job.location] if job.location else [])
    )
    mode = fields.remote if fields else "unknown"
    switched_off = _mode_switched_off(constraints, mode)
    if switched_off:
        reasons.append(switched_off)  # one clear reason; the location rule would only repeat it
    else:
        ok, why = _location_ok(constraints, places, fields)
        if ok is False:
            reasons.append(why)
        elif ok is None:
            uncertain.append(why)
    if mode == "unknown" and not (
        constraints.remote_ok and constraints.hybrid_ok and constraints.onsite_ok
    ):
        uncertain.append("whether the job is remote, hybrid, or onsite wasn't stated")
    if constraints.min_salary_usd is not None:
        top = fields.salary_max_usd if fields else None
        if top is None:
            uncertain.append("no salary was listed")
        elif top < constraints.min_salary_usd:
            reasons.append(f"pay tops out at ${top:,}, below your ${constraints.min_salary_usd:,}")
    me = eligibility or Eligibility()
    _eligibility(fields, me, reasons, uncertain)
    if constraints.hide_no_visa_abroad and fields is not None:
        flag = visa_flag(fields, job.location, me.work_rights)
        if flag is not None and flag.posting_refuses:  # known facts only; "not stated" passes
            reasons.append(f"{flag.text[0].lower()}{flag.text[1:]}")
    return FilterResult(not reasons, reasons, uncertain)


def assess_filters(
    session: Session,
    job: Job,
    constraints: HardConstraints,
    eligibility: Eligibility | None = None,
    aliases: Mapping[str, str] | None = None,
) -> Assessment:
    """Run the filters on `job` and save the result (replacing any earlier one)."""
    saved = job.structured_fields.get("fields")
    fields = JobFields.model_validate(saved) if saved else None
    result = apply_filters(job, fields, constraints, eligibility, aliases)
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    if assessment is None:
        assessment = Assessment(job_id=job.id)
        session.add(assessment)
    assessment.filter_passed = result.passed
    assessment.filter_results = {"reasons": result.reasons, "uncertain": result.uncertain}
    session.commit()
    return assessment


#: Words in a listed location that may mean the job can be done from your places (CH-216).
_REMOTE_HINTS = re.compile(
    r"remote|hybrid|anywhere|work from home|distributed|flexible|multiple|various|nationwide"
    r"|worldwide|global|north america|americas|emea|apac|\bus\b|\busa\b|united states",
    re.I,
)


def prefilter(
    session: Session,
    job: Job,
    constraints: HardConstraints,
    aliases: Mapping[str, str] | None = None,
) -> Assessment | None:
    """Rule a job out *before* any model reads it, from its title, company and listed location.

    Returns the saved failing `Assessment`, or None when the job must be read first. Only
    the cheap, certain rules run: an excluded company, an excluded title word, and a listed
    location that is outside every wanted place and doesn't mention remote or hybrid. Anything
    unsure passes on to extraction, so a good job is never dropped for a guess.
    """
    pre = constraints.model_copy(update={"min_salary_usd": None, "hide_no_visa_abroad": False})
    if not job.location or _REMOTE_HINTS.search(job.location):
        pre = pre.model_copy(update={"locations": []})
    result = apply_filters(job, None, pre, None, aliases)
    if result.passed:
        return None
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    if assessment is None:
        assessment = Assessment(job_id=job.id)
        session.add(assessment)
    assessment.filter_passed = False
    assessment.filter_results = {"reasons": result.reasons, "uncertain": [], "prefiltered": True}
    session.commit()
    return assessment
