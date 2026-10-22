"""Find, create, and merge companies by their key (CH-86). Every importer uses these."""

from __future__ import annotations

import re
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Company, Job, Person, Proposal
from careerhub.core.models.base import utcnow
from careerhub.core.names import company_key

#: The `Proposal.kind` of a suggested company merge.
MERGE_KIND = "company_merge"


def get_or_create_company(
    session: Session, name: str, aliases: Mapping[str, str] | None = None
) -> Company | None:
    """Return the company called `name` (matched by key), creating it; None for a blank name."""
    key = company_key(name, aliases)
    if not key:
        return None
    company = session.scalars(select(Company).where(Company.name_key == key)).first()
    if company is None:
        company = Company(name=" ".join(name.split()), name_key=key)
        session.add(company)
        session.flush()
    return company


def find_companies(
    session: Session, text: str, aliases: Mapping[str, str] | None = None
) -> list[Company]:
    """Return the companies matching `text`: the exact key if there is one, else whole-word hits.

    "Meta" does not match "Metabase" (only whole words count), and several matches are all
    returned so the caller can report the ambiguity instead of picking one.
    """
    key = company_key(text, aliases)
    if not key:
        return []
    exact = session.scalars(select(Company).where(Company.name_key == key)).first()
    if exact is not None:
        return [exact]
    word = re.compile(rf"(?<!\w){re.escape(key)}(?!\w)")
    everyone = session.scalars(select(Company).order_by(Company.name_key)).all()
    return [c for c in everyone if word.search(c.name_key)]


def merge_companies(session: Session, keep: Company, drop: Company) -> None:
    """Move everything from `drop` to `keep` and delete `drop`."""
    for job in session.scalars(select(Job).where(Job.company_id == drop.id)):
        job.company_id = keep.id
    for person in session.scalars(select(Person).where(Person.company_id == drop.id)):
        person.company_id = keep.id
    keep.notes = "\n".join(t for t in (keep.notes, drop.notes) if t) or None
    keep.size = keep.size or drop.size
    keep.industry = keep.industry or drop.industry
    keep.domains = sorted({*keep.domains, *drop.domains})
    keep.ats_board_ids = {**drop.ats_board_ids, **keep.ats_board_ids}
    session.flush()
    session.delete(drop)
    session.flush()


def _near_miss(a: str, b: str) -> bool:
    """Say whether one key is the other plus one or two whole words ("acme", "acme robotics")."""
    short, long_ = sorted((a.split(), b.split()), key=len)
    extra = len(long_) - len(short)
    return 1 <= extra <= 2 and long_[: len(short)] == short


def suggest_merges(session: Session) -> int:
    """Record a pending `company_merge` proposal for each pair of near-identical companies.

    "Acme" and "Acme Robotics" may be one employer or two; only the user can say, so this never
    merges. A pair that already has a proposal (even a rejected one) is not suggested again.
    Returns how many new suggestions were made.
    """
    companies = list(session.scalars(select(Company).order_by(Company.id)))
    seen = {p.title for p in session.scalars(select(Proposal).where(Proposal.kind == MERGE_KIND))}
    made = 0
    for i, first in enumerate(companies):
        for second in companies[i + 1 :]:
            if not _near_miss(first.name_key, second.name_key):
                continue
            keep, drop = sorted((first, second), key=lambda c: len(c.name_key))
            title = f"Merge {drop.name} into {keep.name}?"
            if title in seen:
                continue
            session.add(
                Proposal(
                    kind=MERGE_KIND,
                    title=title,
                    body=f"keep={keep.id} drop={drop.id}",
                    origin="agent",
                )
            )
            seen.add(title)
            made += 1
    session.commit()
    return made


def pending_merges(session: Session) -> list[Proposal]:
    """Return the merge suggestions waiting for the user's answer."""
    return list(
        session.scalars(
            select(Proposal)
            .where(Proposal.kind == MERGE_KIND, Proposal.status == "pending")
            .order_by(Proposal.id)
        )
    )


def decide_merge(session: Session, proposal_id: int, accept: bool) -> str:
    """Accept (merge) or reject suggestion `proposal_id`; return a sentence saying what happened.

    The shorter name is kept. If either company has since been merged or removed, the suggestion
    is closed without changing anything.
    """
    proposal = session.get(Proposal, proposal_id)
    if proposal is None or proposal.kind != MERGE_KIND or proposal.status != "pending":
        raise LookupError(f"No pending merge suggestion {proposal_id}.")
    proposal.status = "accepted" if accept else "rejected"
    proposal.decided_at = utcnow()
    message = f"Rejected: {proposal.title}"
    if accept:
        ids = dict(part.split("=") for part in (proposal.body or "").split())
        keep = session.get(Company, int(ids.get("keep", 0)))
        drop = session.get(Company, int(ids.get("drop", 0)))
        if keep is None or drop is None:
            message = "One of those companies is already gone; nothing merged."
        else:
            merge_companies(session, keep, drop)
            message = f"Merged {drop.name} into {keep.name}."
    session.commit()
    return message
