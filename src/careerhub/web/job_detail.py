"""What the Job detail page shows for one job (CH-141). Read-only; nothing is changed or sent.

Every part has an empty state: a job that was never scored has no rubric breakdown, one nobody
knows at has no contacts, and so on. The posting text is untrusted and is only ever shown as
escaped plain text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Job, Source, utcnow
from careerhub.core.settings import load_settings
from careerhub.modules.jobs.muse import VIA
from careerhub.modules.jobs.reactions import current_reaction
from careerhub.modules.jobs.scoring import current_rank_adjustments
from careerhub.modules.network import WarmPath, warm_paths
from careerhub.modules.network.schools import alumni_search_link, my_schools
from careerhub.web.shortlist import (
    deadline_of,
    first_sentence,
    from_muse,
    posting_footnotes,
    safe_link,
    salary_text,
    stated_fields,
    tailoring_notes,
)

MAX_CONTACTS = 5


@dataclass(frozen=True)
class JobDetail:
    """One job, ready for the template."""

    job_id: int
    title: str
    company: str
    location: str | None
    salary: str | None
    link: str | None
    status: str
    first_seen: date
    score: float | None
    reason: str | None
    explanation: str | None
    criteria: list[dict[str, Any]]
    summary: str | None
    role_focus: str | None
    filter_reasons: list[str]
    filter_uncertain: list[str]
    tailoring: dict[str, list[str]] | None
    contacts: list[WarmPath]
    alumni_link: str | None
    alumni_school: str | None
    deadline: date | None
    footnotes: tuple[str, ...]
    source_credit: str | None
    reaction: str | None
    posting: str
    extra: dict[str, Any] = field(default_factory=dict)


def gather_job(session: Session, job_id: int, now: datetime | None = None) -> JobDetail | None:
    """Collect everything about job `job_id`, or None if there is no such job."""
    job = session.get(Job, job_id)
    if job is None:
        return None
    now = now or utcnow()
    assessment = session.scalars(select(Assessment).where(Assessment.job_id == job.id)).first()
    fit = assessment.fit_score if assessment else None
    results = (assessment.filter_results if assessment else None) or {}
    adjustments = current_rank_adjustments()
    slug = load_settings().linkedin_school_slug
    schools = my_schools(session) if slug else []
    fields = stated_fields(job)
    source = session.get(Source, job.source_id)
    return JobDetail(
        job_id=job.id,
        title=job.title,
        company=job.company.name,
        location=job.location,
        salary=salary_text(job),
        link=safe_link(job.url),
        status=job.status,
        first_seen=job.first_seen_at.date(),
        score=fit,
        reason=first_sentence(assessment.explanation)
        if assessment and assessment.explanation
        else None,
        explanation=assessment.explanation if assessment else None,
        criteria=list(assessment.criteria_scores or []) if assessment else [],
        summary=_text(fields.get("summary")),
        role_focus=_text(fields.get("role_focus")),
        filter_reasons=[str(r) for r in results.get("reasons", [])],
        filter_uncertain=[str(r) for r in results.get("uncertain", [])],
        tailoring=tailoring_notes(assessment) if assessment else None,
        contacts=warm_paths(session, job.company_id, limit=MAX_CONTACTS),
        alumni_link=alumni_search_link(slug, job.company.name),
        alumni_school=schools[0] if schools else None,
        deadline=deadline_of(job),
        footnotes=posting_footnotes(
            job, now, adjustments["internship_penalty"], adjustments["stale_after_days"]
        ),
        source_credit=VIA if from_muse(session, job) else (source.name if source else None),
        reaction=current_reaction(session, job.id),
        posting=job.raw_text or "",
    )


def _text(value: object) -> str | None:
    """Return `value` as text if it is a non-empty string."""
    return value if isinstance(value, str) and value.strip() else None
