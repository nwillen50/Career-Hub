"""What needs the user's attention: the menu-bar count and error state (CH-146).

The count is follow-ups due plus top jobs not yet reacted to (PRD, "Getting the user's attention").
It is the same number Today shows, so the icon never disagrees with the page. The state is plain
data; the real menu-bar helper runs on the user's Mac and reads it (`hub attention --json`). It
holds counts and a yes/no only: no company, title, or person ever appears in it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import RunLog, utcnow
from careerhub.web.today import gather_today

#: The only menu item (runs are scheduled; opening Today is the daily action).
MENU_ITEMS = ("Open Today",)


@dataclass(frozen=True)
class AttentionState:
    """The counts behind the menu-bar icon."""

    follow_ups_due: int
    new_top_jobs: int
    #: True when the latest nightly run failed (the icon shows a red dot).
    error: bool

    @property
    def count(self) -> int:
        """The number on the icon."""
        return self.follow_ups_due + self.new_top_jobs

    @property
    def label(self) -> str:
        """The icon's text: the count, or "!" with it when the last run failed."""
        return f"! {self.count}" if self.error else str(self.count)

    @property
    def description(self) -> str:
        """The icon's tooltip, in words (so the red dot is never colour alone)."""
        parts = [f"{self.follow_ups_due} follow-ups due", f"{self.new_top_jobs} new top jobs"]
        if self.error:
            parts.append("the last nightly run failed")
        return ", ".join(parts)

    def as_json(self) -> dict[str, object]:
        """Return the state for the helper: counts, flag, label, tooltip, and menu."""
        return {
            **asdict(self),
            "count": self.count,
            "label": self.label,
            "description": self.description,
            "menu": list(MENU_ITEMS),
        }


def attention_state(session: Session, now: datetime | None = None) -> AttentionState:
    """Work out the icon's count and error state as of `now` (reads only; changes nothing)."""
    now = now or utcnow()
    today = gather_today(session, now)
    latest = session.scalars(
        select(RunLog)
        .where(RunLog.step == "nightly", RunLog.status != "running")
        .order_by(RunLog.started_at.desc(), RunLog.id.desc())
        .limit(1)
    ).first()
    return AttentionState(
        follow_ups_due=len(today.follow_ups) + today.more_overdue,
        new_top_jobs=len(today.jobs),
        error=latest is not None and latest.status == "failed",
    )
