"""'Check the effect' of an accepted rubric change: the ranking eval before and after (CH-172).

After a rubric proposal is accepted, the user may press a button that runs the ranking eval
(`rank_evals.run_ranking_eval`) on their own labeled postings twice, once with the rubric as it
was (the `.before-<time>` copy kept when the change was accepted) and once with the rubric now,
and shows the filter eval, the top-10 eval, and the ordering share side by side. It spends model
calls, so it is only ever run from the button, after the page says roughly how many. It needs
labeled postings (`evals/ranking.yaml` in the data folder, CH-7): without them it says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Proposal
from careerhub.core.paths import data_dir
from careerhub.llm import Router
from careerhub.modules.jobs.evals import EvalError
from careerhub.modules.jobs.rank_evals import LabeledPosting, RankingReport, load_labeled
from careerhub.modules.jobs.rank_evals import run_ranking_eval as _run
from careerhub.modules.proposals import RUBRIC_KIND
from careerhub.profile.files import ProfileFileError, parse_yaml, validate_model
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import Rubric, rubric_path

#: The postings the user labeled great, okay, or no. About 30 make the numbers mean something.
MEANINGFUL_LABELS = 30
#: Per posting and per rubric: one reading of the posting and one scoring (saved answers lower it).
CALLS_PER_POSTING = 2


def labels_path() -> Path:
    """Return where the user's labeled postings live (the file may not exist)."""
    return data_dir(create=False) / "evals" / "ranking.yaml"


def load_labels() -> list[LabeledPosting] | None:
    """Return the labeled postings, or None when there is no file. Raises `EvalError` if broken."""
    path = labels_path()
    return load_labeled(path) if path.exists() else None


def estimate_calls(postings: int) -> int:
    """Return the most model calls a check can make: two rubrics, each reading and scoring."""
    return postings * CALLS_PER_POSTING * 2


def accepted_rubric_changes(session: Session, limit: int = 3) -> list[Proposal]:
    """Return the newest accepted rubric proposals (the ones that can be checked)."""
    return list(
        session.scalars(
            select(Proposal)
            .where(Proposal.kind == RUBRIC_KIND, Proposal.status == "accepted")
            .order_by(Proposal.decided_at.desc(), Proposal.id.desc())
            .limit(limit)
        )
    )


def rubric_before(proposal: Proposal) -> Rubric | None:
    """Return the rubric as it was before `proposal` was accepted, from the backup kept then.

    The backup is named for the moment of acceptance (`proposals.free_backup_name`); None when it
    is gone or unreadable.
    """
    if proposal.decided_at is None:
        return None
    stamp = f"{proposal.decided_at:%Y%m%d-%H%M%S-%f}"
    path = rubric_path()
    for backup in sorted(path.parent.glob(f"{path.name}.before-{stamp}*")):
        try:
            return validate_model(
                Rubric, parse_yaml(backup.read_text(encoding="utf-8"), backup.name), backup.name
            )
        except (ProfileFileError, OSError, ValueError):
            return None
    return None


@dataclass(frozen=True)
class EffectReport:
    """The ranking eval with the old and the new rubric."""

    postings: int
    before: RankingReport
    after: RankingReport

    def rows(self) -> list[tuple[str, str, str]]:
        """Return (measure, before, after) lines for the page."""

        def pairs(report: RankingReport) -> str:
            share = report.pair_accuracy
            return "n/a" if share is None else f"{share:.0%}"

        lines = [
            (
                "Great postings filtered out (should be 0)",
                str(len(self.before.great_filtered_out)),
                str(len(self.after.great_filtered_out)),
            ),
            (
                "Postings labeled no among the top 10 (1 or fewer passes)",
                str(len(self.before.no_in_top)),
                str(len(self.after.no_in_top)),
            ),
            ("Label pairs in the right order", pairs(self.before), pairs(self.after)),
        ]
        for label in ("great", "okay", "no"):
            old, new = self.before.mean_score.get(label), self.after.mean_score.get(label)
            lines.append(
                (
                    f"Average score of postings labeled {label}",
                    "n/a" if old is None else f"{old:.1f}",
                    "n/a" if new is None else f"{new:.1f}",
                )
            )
        return lines


def check_effect(
    router: Router,
    labels: Sequence[LabeledPosting],
    doc: ProfileDoc,
    old: Rubric,
    new: Rubric,
    constraints: HardConstraints,
    extract_routes: Sequence[str],
    score_routes: Sequence[str],
) -> EffectReport:
    """Run the ranking eval with both rubrics on `labels` (spends model calls)."""
    if not labels:
        raise EvalError("There are no labeled postings to check against.")
    before = _run(router, labels, doc, old, constraints, extract_routes, score_routes)
    after = _run(router, labels, doc, new, constraints, extract_routes, score_routes)
    return EffectReport(len(labels), before, after)
