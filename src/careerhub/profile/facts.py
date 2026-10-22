"""Review extracted facts and use the accepted ones (CH-110).

Facts read from the resume and transcript (CH-91) are **pending** until the user accepts them.
`accepted_facts` merges the newest accepted resume and transcript extractions; `facts_text`
turns them into a short block for scoring and tailoring prompts. Anything typed into `profile.md`
wins: a skill or school already there is not repeated, and extracted education for a school the
user listed is dropped.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import ProfileExtraction
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.models import ProfileDoc

MAX_FACTS_CHARS = 3_000


class UnknownExtraction(Exception):
    """No pending extraction has that number."""


def pending(session: Session) -> list[ProfileExtraction]:
    """Return the extractions waiting for a decision, oldest first."""
    query = select(ProfileExtraction).where(ProfileExtraction.status == "pending")
    return list(session.scalars(query.order_by(ProfileExtraction.id)))


def decide(session: Session, extraction_id: int, accept: bool) -> ProfileExtraction:
    """Accept or reject a pending extraction. Raises `UnknownExtraction` if it isn't pending."""
    row = session.get(ProfileExtraction, extraction_id)
    if row is None or row.status != "pending":
        raise UnknownExtraction(f"No pending facts numbered {extraction_id}.")
    row.status = "accepted" if accept else "rejected"
    session.commit()
    return row


def counts(session: Session) -> dict[str, int]:
    """Return how many extractions are in each status (numbers only)."""
    rows = session.scalars(select(ProfileExtraction.status)).all()
    return {s: rows.count(s) for s in ("pending", "accepted", "rejected")}


def accepted_facts(session: Session) -> ProfileFacts | None:
    """Merge the newest accepted resume and transcript facts; None if nothing is accepted."""
    parts: list[ProfileFacts] = []
    for kind in ("resume", "transcript"):
        row = session.scalars(
            select(ProfileExtraction)
            .where(ProfileExtraction.kind == kind, ProfileExtraction.status == "accepted")
            .order_by(ProfileExtraction.id.desc())
        ).first()
        if row is not None:
            parts.append(ProfileFacts.model_validate(row.facts))
    if not parts:
        return None
    merged = ProfileFacts()
    for part in parts:
        merged.experiences += part.experiences
        merged.education += part.education
        merged.courses += part.courses
        merged.skills += part.skills
        merged.projects += part.projects
        merged.awards += part.awards
        merged.gpa = merged.gpa if merged.gpa is not None else part.gpa
    return merged


def facts_text(facts: ProfileFacts | None, doc: ProfileDoc) -> str:
    """Describe accepted facts for a prompt, leaving out what `profile.md` already says."""
    if facts is None:
        return ""
    typed_schools = {e.school.strip().lower() for e in doc.education}
    typed_skills = {s.strip().lower() for s in doc.skills}
    lines: list[str] = []
    for job in facts.experiences:
        when = f" ({job.start or '?'} to {job.end or '?'})" if job.start or job.end else ""
        points = "; ".join(job.points)
        lines.append(f"Experience: {job.role} at {job.organization or 'unknown'}{when}. {points}")
    for school in facts.education:
        if school.school.strip().lower() not in typed_schools:
            lines.append(f"Education: {school.degree or 'degree not stated'}, {school.school}")
    if facts.gpa is not None:
        lines.append(f"GPA: {facts.gpa}")
    if facts.courses:
        courses = ", ".join(
            f"{c.code + ' ' if c.code else ''}{c.title} ({c.grade or '-'})" for c in facts.courses
        )
        lines.append(f"Courses: {courses}")
    extra_skills = [s for s in facts.skills if s.strip().lower() not in typed_skills]
    if extra_skills:
        lines.append(f"More skills: {', '.join(extra_skills)}")
    lines += [f"Project: {p.name}. {p.description or ''}".strip() for p in facts.projects]
    if facts.awards:
        lines.append(f"Awards: {', '.join(facts.awards)}")
    return "\n".join(lines)[:MAX_FACTS_CHARS]
