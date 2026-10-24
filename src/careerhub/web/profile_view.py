"""What the Profile tab shows (CH-195): what Career Hub knows about you, and what it still lacks.

Read-only except for accepting or rejecting facts read from the resume and transcript, the same
decision `hub profile facts` makes. Everything here stays on this Mac: the page is served on
127.0.0.1 only and nothing on it is sent anywhere. The resume and transcript text are never shown,
only what kind of file each is. Editing happens in the files; each section says which one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from careerhub.core.settings import load_settings
from careerhub.profile.documents import read_document
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import counts, facts_text, pending
from careerhub.profile.files import load_preferences, load_profile_doc
from careerhub.profile.inputs import find_resume, find_transcript
from careerhub.profile.models import Preferences, ProfileDoc
from careerhub.profile.profile import DocumentInfo
from careerhub.profile.readiness import current_readiness
from careerhub.profile.rubric import load_rubric


@dataclass(frozen=True)
class PendingFacts:
    """One set of facts waiting for Accept or Reject."""

    id: int
    kind: str
    lines: list[str]


@dataclass(frozen=True)
class ProfileView:
    """Everything the Profile tab shows. A section that has no file yet is None or empty."""

    scoring_on: bool
    scoring_since: str | None
    missing: list[str]
    name: str | None = None
    education: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    about: str = ""
    has_profile_file: bool = False
    constraints: list[tuple[str, str]] = field(default_factory=list)
    has_preferences_file: bool = False
    plan: list[tuple[str, str]] = field(default_factory=list)
    rubric: list[tuple[str, str, str]] = field(default_factory=list)
    rubric_version: int | None = None
    documents: list[tuple[str, str]] = field(default_factory=list)
    fact_counts: dict[str, int] = field(default_factory=dict)
    pending: list[PendingFacts] = field(default_factory=list)


def _yes_no(value: bool) -> str:
    return "allowed" if value else "not allowed"


def _list(items: list[str]) -> str:
    return ", ".join(items) if items else "none set"


def _education(doc: ProfileDoc) -> list[str]:
    return [
        f"{e.degree}, {e.school}" + (f" (expected {e.graduation:%b %Y})" if e.graduation else "")
        for e in doc.education
    ]


def _constraint_rows(preferences: Preferences) -> list[tuple[str, str]]:
    hard, timeline = preferences.hard_constraints, preferences.timeline
    modes = {"remote": hard.remote_ok, "hybrid": hard.hybrid_ok, "on-site": hard.onsite_ok}
    return [
        ("Locations", _list(hard.locations)),
        *[(f"Work mode: {name}", _yes_no(ok)) for name, ok in modes.items()],
        ("Minimum salary", f"${hard.min_salary_usd:,}" if hard.min_salary_usd else "none set"),
        ("Excluded companies", _list(hard.excluded_companies)),
        ("Excluded title words", _list(hard.excluded_title_words)),
        ("Earliest start", str(timeline.earliest_start or "none set")),
        ("Latest start", str(timeline.latest_start or "none set")),
    ]


def _plan_rows(preferences: Preferences) -> list[tuple[str, str]]:
    plan = preferences.plan
    hours = plan.working_hours
    return [
        ("Time for the search each week", f"{preferences.weekly_time_budget_hours:g} hours"),
        ("An application block", f"{plan.apply_minutes} minutes"),
        ("An outreach block", f"{plan.outreach_minutes} minutes"),
        ("Most of the week's budget in one day", f"{plan.daily_share:.0%}"),
        ("Shortest free gap used", f"{plan.min_gap_minutes} minutes"),
        ("Tasks in a plan", str(plan.tasks_per_plan)),
        ("Working hours", f"{hours.start} to {hours.end}"),
    ]


def _fact_lines(facts: object, doc: ProfileDoc | None) -> list[str]:
    """Describe one set of stored facts; a set that no longer fits the schema says so."""
    try:
        shown = facts_text(ProfileFacts.model_validate(facts), doc or ProfileDoc(name=""))
    except ValueError:
        return ["These facts were saved by an older version and cannot be shown. Reject them."]
    return (shown or "(nothing found)").splitlines()


def gather_profile(session: Session) -> ProfileView:
    """Read the profile files and the pending facts. Raises `ProfileFileError` on a broken file."""
    doc, preferences, rubric = load_profile_doc(), load_preferences(), load_rubric()
    readiness = current_readiness(session, load_settings().profile_min_skills)
    resume, _ = find_resume()
    transcript, _ = find_transcript()
    files = (("Resume", resume.main if resume else None), ("Transcript", transcript))
    return ProfileView(
        scoring_on=readiness.on,
        scoring_since=readiness.since.isoformat() if readiness.since else None,
        missing=list(readiness.missing),
        has_profile_file=doc is not None,
        has_preferences_file=preferences is not None,
        name=doc.name if doc else None,
        education=_education(doc) if doc else [],
        skills=list(doc.skills) if doc else [],
        interests=list(doc.interests) if doc else [],
        about=doc.about.strip() if doc else "",
        constraints=_constraint_rows(preferences) if preferences else [],
        plan=_plan_rows(preferences) if preferences else [],
        rubric=[(c.name, f"{c.weight:g}", c.description) for c in rubric.criteria]
        if rubric
        else [],
        rubric_version=rubric.version if rubric else None,
        documents=[
            (label, DocumentInfo.of(read_document(path)).describe())
            for label, path in files
            if path is not None
        ],
        fact_counts=counts(session),
        pending=[
            PendingFacts(row.id, row.kind, _fact_lines(row.facts, doc)) for row in pending(session)
        ],
    )
