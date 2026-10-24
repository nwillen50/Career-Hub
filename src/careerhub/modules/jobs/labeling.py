"""Label postings great, okay, or no, one at a time, for the ranking evals (CH-201, feeds CH-7).

The labels are saved in `evals/ranking.yaml` inside the data folder, in the format of
`evals/ranking/sample.yaml` that `rank_evals.load_labeled` reads, so they can be used by the
ranking eval and by "Check the effect" (CH-172) without any conversion. A label is separate from a
reaction: labeling a posting does not mark it interested, and reacting does not label it. The
labeled text is the public posting, never anything from the profile.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.files import write_private
from careerhub.core.models import Assessment, Feedback, Job
from careerhub.core.paths import data_dir
from careerhub.modules.jobs.store import only_visible_copies

LABELS = ("great", "okay", "no")
MAX_REASON = 300
MAX_TEXT = 12_000


class LabelError(ValueError):
    """The label can't be saved; the message says why in plain words."""


def labels_path() -> Path:
    """Return where the labeled postings live (the file may not exist)."""
    return data_dir(create=False) / "evals" / "ranking.yaml"


def label_id(job: Job) -> str:
    """Return the id a job's label is saved under."""
    return f"job-{job.id}"


def _read() -> list[dict[str, Any]]:
    path = labels_path()
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cases = data["cases"]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        raise LabelError(
            "evals/ranking.yaml could not be read, so nothing was changed. Fix or move it first."
        ) from None
    if not isinstance(cases, list):
        raise LabelError("evals/ranking.yaml has no list of cases, so nothing was changed.")
    return [dict(c) for c in cases if isinstance(c, dict)]


def labeled_ids() -> set[str]:
    """Return the ids already labeled (none without a file; `LabelError` if it is broken)."""
    return {str(case.get("id")) for case in _read()}


def counts() -> dict[str, int]:
    """Return how many postings carry each label."""
    found = {label: 0 for label in LABELS}
    for case in _read():
        label = "no" if case.get("label") is False else str(case.get("label"))
        if label in found:
            found[label] += 1
    return found


def next_to_label(session: Session, skip: int = 0) -> Job | None:
    """Return the next open job with no reaction and no label (best scored first), or None.

    `skip` passes over that many candidates, for the Skip link.
    """
    done = labeled_ids()
    reacted = select(Feedback.job_id)
    query = (
        select(Job)
        .join(Assessment, Assessment.job_id == Job.id)
        .where(Job.status != "closed", Job.id.not_in(reacted))
        .order_by(Assessment.fit_score.desc().nulls_last(), Job.id)
    )
    candidates = [
        job for job in session.scalars(only_visible_copies(query)) if label_id(job) not in done
    ]
    return candidates[skip] if 0 <= skip < len(candidates) else None


def save_label(job: Job, label: str, reason: str) -> None:
    """Add (or replace) `job`'s label in the file. Raises `LabelError` for a bad label or file."""
    if label not in LABELS:
        raise LabelError("Choose great, okay, or no.")
    note = " ".join(reason.split())
    if len(note) > MAX_REASON:
        raise LabelError(f"Keep the reason under {MAX_REASON} characters.")
    cases = [c for c in _read() if c.get("id") != label_id(job)]
    cases.append(
        {
            "id": label_id(job),
            "company": job.company.name,
            "title": job.title,
            "location": job.location,
            "label": label,
            "reason": note or "(no reason given)",
            "text": (job.raw_text or job.title)[:MAX_TEXT],
        }
    )
    path = labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    write_private(temporary, yaml.safe_dump({"cases": cases}, sort_keys=False, allow_unicode=True))
    os.replace(temporary, path)
