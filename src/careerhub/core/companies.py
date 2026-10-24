"""Find, create, and merge companies by their key (CH-86). Every importer uses these."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote

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
    company = _by_key(session, key)
    if company is None:
        company = Company(name=" ".join(name.split()), name_key=key)
        session.add(company)
        session.flush()
    return company


def _by_key(session: Session, key: str) -> Company | None:
    """Return the company with this key, or the one a company of this key was merged into."""
    found = session.scalars(select(Company).where(Company.name_key == key)).first()
    if found is not None:
        return found
    for company in session.scalars(select(Company).where(Company.merged_keys.is_not(None))):
        if key in (company.merged_keys or []):
            return company
    return None


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
    exact = _by_key(session, key)
    if exact is not None:
        return [exact]
    word = re.compile(rf"(?<!\w){re.escape(key)}(?!\w)")
    everyone = session.scalars(select(Company).order_by(Company.name_key)).all()
    return [c for c in everyone if word.search(c.name_key)]


def merge_companies(
    session: Session, keep: Company, drop: Company, aliases: Mapping[str, str] | None = None
) -> None:
    """Move everything from `drop` to `keep` and delete `drop`.

    `drop`'s name stays findable (`merged_keys`), so a later import under the old name joins `keep`
    instead of creating the company again. Moved jobs get new duplicate keys, and the same posting
    seen by two sources is linked.
    """
    from careerhub.modules.jobs.store import (
        dedupe_key,
        link_duplicates,
    )  # core must not import jobs at load

    moved = list(session.scalars(select(Job).where(Job.company_id == drop.id)))
    people = list(session.scalars(select(Person).where(Person.company_id == drop.id)))
    entry: dict[str, Any] = {
        "name": drop.name,
        "name_key": drop.name_key,
        "merged_keys": list(drop.merged_keys or []),
        "domains": list(drop.domains),
        "ats_board_ids": dict(drop.ats_board_ids),
        "size": drop.size,
        "industry": drop.industry,
        "notes": drop.notes,
        "job_ids": [job.id for job in moved],
        "person_ids": [person.id for person in people],
        #: What `drop` had itself absorbed, so splitting `drop` back out restores that too.
        "carried": list(drop.merge_log or []),
    }
    keep.merge_log = [*(keep.merge_log or []), *entry["carried"], entry]
    for job in moved:
        job.company_id = keep.id
        job.dedupe_key = dedupe_key(keep.name, job.title, job.location, aliases)
    keep.merged_keys = sorted({*(keep.merged_keys or []), drop.name_key, *(drop.merged_keys or [])})
    for person in people:
        person.company_id = keep.id
    keep.notes = "\n".join(t for t in (keep.notes, drop.notes) if t) or None
    keep.size = keep.size or drop.size
    keep.industry = keep.industry or drop.industry
    keep.domains = sorted({*keep.domains, *drop.domains})
    keep.ats_board_ids = {**drop.ats_board_ids, **keep.ats_board_ids}
    session.flush()
    session.delete(drop)
    session.flush()
    link_duplicates(session, [job.dedupe_key for job in moved])


def _source_name(job: Job) -> str:
    """Return the company name the source gave for `job` (empty when its record has none)."""
    payload = job.raw_payload or {}
    company = payload.get("company")
    return str(
        payload.get("company_name")
        or (company.get("name") if isinstance(company, dict) else company)
        or ""
    )


def list_merges(session: Session) -> list[tuple[Company, dict[str, Any]]]:
    """Return every recorded merge as (company it went into, its log entry)."""
    return [
        (company, entry)
        for company in session.scalars(select(Company).order_by(Company.id))
        for entry in company.merge_log or []
    ]


def split_company(
    session: Session, company: Company, name: str, aliases: Mapping[str, str] | None = None
) -> str:
    """Undo the merge of `name` into `company`; return a sentence saying what happened.

    Restores `name` as its own company, takes its old name out of `merged_keys`, moves back the jobs
    and people the merge moved (a job or person that has since moved elsewhere stays), recomputes
    their duplicate keys and links, and records the pair as "never merge" so it is not suggested
    again. A merge made before the log existed has no saved ids: jobs are matched on the company
    name their source gave, and people stay where they are (the message says so).
    Raises `LookupError` when `name` was not merged into `company`.
    """
    from careerhub.modules.jobs.store import dedupe_key, link_duplicates

    key = company_key(name, aliases)
    log = list(company.merge_log or [])
    entry = next((e for e in log if e["name_key"] == key), None)
    if entry is None and key not in (company.merged_keys or []):
        raise LookupError(f"{name} was never merged into {company.name}.")
    restored = Company(
        name=entry["name"] if entry else " ".join(name.split()),
        name_key=key,
        domains=list(entry["domains"]) if entry else [],
        ats_board_ids=dict(entry["ats_board_ids"]) if entry else {},
        size=entry["size"] if entry else None,
        industry=entry["industry"] if entry else None,
        notes=entry["notes"] if entry else None,
        merged_keys=(entry["merged_keys"] or None) if entry else None,
        merge_log=(list(entry.get("carried") or []) or None) if entry else None,
    )
    session.add(restored)
    session.flush()
    own_jobs = list(session.scalars(select(Job).where(Job.company_id == company.id)))
    if entry:
        wanted = set(entry["job_ids"])
        jobs = [job for job in own_jobs if job.id in wanted]
        wanted_people = set(entry["person_ids"])
    else:
        jobs = [job for job in own_jobs if company_key(_source_name(job), aliases) == key]
        wanted_people = set()
    old_keys = [job.dedupe_key for job in jobs]
    for job in jobs:
        job.company_id = restored.id
        job.duplicate_of_id = None
        job.dedupe_key = dedupe_key(restored.name, job.title, job.location, aliases)
    people = list(session.scalars(select(Person).where(Person.company_id == company.id)))
    for person in people:
        if person.id in wanted_people:
            person.company_id = restored.id
    gone = {key, *(entry["merged_keys"] if entry else [])}
    company.merged_keys = [k for k in company.merged_keys or [] if k not in gone] or None
    carried = {e["name_key"] for e in (entry.get("carried") or [])} if entry else set()
    company.merge_log = [e for e in log if e["name_key"] not in {key, *carried}] or None
    _never_merge(session, company, restored)
    session.flush()
    link_duplicates(session, [*old_keys, *(job.dedupe_key for job in jobs)])
    # Jobs left behind that were linked to a moved original are their own originals again.
    moved_ids = {job.id for job in jobs}
    for job in own_jobs:
        if job.duplicate_of_id in moved_ids:
            job.duplicate_of_id = None
    link_duplicates(session, [job.dedupe_key for job in own_jobs])
    session.commit()
    note = "" if entry else " This merge was made before merges were logged, so its people stayed."
    return f"Split {restored.name} from {company.name}: {len(jobs)} job(s) moved back.{note}"


def _never_merge(session: Session, first: Company, second: Company) -> None:
    """Record a rejected merge suggestion for the pair, so `suggest_merges` leaves it alone."""
    keep, drop = sorted((first, second), key=lambda c: len(c.name_key))
    session.add(
        Proposal(
            kind=MERGE_KIND,
            title=f"Merge {drop.name} into {keep.name}?",
            body="never merge (split by hand)",
            origin="agent",
            status="rejected",
            decided_at=utcnow(),
        )
    )


def _same_keys(ids: Mapping[str, str], keep: Company, drop: Company) -> bool:
    """Say whether the companies at the saved ids still have the keys the suggestion names."""
    return all(
        wanted is None or unquote(wanted) == company.name_key
        for wanted, company in ((ids.get("keep_key"), keep), (ids.get("drop_key"), drop))
    )


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
    # A near miss shares its first word, so only compare companies within the same group.
    groups: dict[str, list[Company]] = {}
    for company in companies:
        groups.setdefault(company.name_key.split(" ")[0], []).append(company)
    pairs = [
        (first, second)
        for group in groups.values()
        for i, first in enumerate(group)
        for second in group[i + 1 :]
    ]
    for first, second in sorted(pairs, key=lambda pair: (pair[0].id, pair[1].id)):
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
                body=(
                    f"keep={keep.id} drop={drop.id} "
                    f"keep_key={quote(keep.name_key)} drop_key={quote(drop.name_key)}"
                ),
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


def decide_merge(
    session: Session, proposal_id: int, accept: bool, aliases: Mapping[str, str] | None = None
) -> str:
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
        # SQLite reuses ids, so a suggestion also checks the keys it was made for (CH-154)
        if keep is None or drop is None or not _same_keys(ids, keep, drop):
            message = "One of those companies is already gone; nothing merged."
        else:
            merge_companies(session, keep, drop, aliases)
            message = f"Merged {drop.name} into {keep.name}."
    session.commit()
    return message
