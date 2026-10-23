"""What the Today page shows (CH-139): top jobs, follow-ups due, deadlines this week, last run.

Read-only. It reuses `gather_shortlist` for the jobs (so the cards match the nightly page), adds
follow-ups from logged interactions, and the deadlines of the ranked jobs. Nothing here changes
data, and nothing is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Interaction, Person, utcnow
from careerhub.core.settings import load_settings
from careerhub.modules.jobs.reactions import reacted_job_ids
from careerhub.modules.jobs.scoring import ranked_jobs
from careerhub.web.shortlist import (
    ShortlistData,
    ShortlistEntry,
    first_sentence,
    gather_shortlist,
    safe_link,
)

MAX_TOP_JOBS = 5
MAX_FOLLOW_UPS = 5
#: How many ranked jobs are checked for a deadline this week.
MAX_DEADLINE_SCAN = 200
#: Section names a visitor can show one at a time (the wireframe's chips), "all" shows every one.
SECTIONS = ("all", "top", "exploration", "followups", "deadlines")


@dataclass(frozen=True)
class JobCard:
    """One job as the template needs it: the entry plus a safe link and a one-sentence reason."""

    entry: ShortlistEntry
    link: str | None
    first_sentence: str

    def __getattr__(self, name: str) -> object:
        """Let the template read the entry's fields directly (title, company, score...)."""
        return getattr(self.entry, name)


def job_cards(entries: list[ShortlistEntry]) -> list[JobCard]:
    """Build the cards for `entries`; a link is kept only if it is http or https."""
    return [JobCard(e, safe_link(e.url), first_sentence(e.explanation)) for e in entries]


@dataclass(frozen=True)
class FollowUp:
    """A follow-up that is due: who, where, what was said, and when it was due."""

    person: str
    company: str | None
    summary: str | None
    due: datetime
    overdue_days: int


@dataclass(frozen=True)
class Deadline:
    """A ranked job with an application deadline in the coming week."""

    title: str
    company: str
    when: date
    link: str | None


@dataclass(frozen=True)
class TodayData:
    """Everything the Today page shows."""

    now: datetime
    shortlist: ShortlistData
    follow_ups: list[FollowUp] = field(default_factory=list)
    more_overdue: int = 0
    deadlines: list[Deadline] = field(default_factory=list)
    #: `now` in the user's timezone (the page's date and "overdue" days come from this).
    local_now: datetime | None = None

    @property
    def jobs(self) -> list[ShortlistEntry]:
        """Return the top jobs shown (never more than five)."""
        return self.shortlist.entries[:MAX_TOP_JOBS]


def clean_section(value: str | None) -> str:
    """Return a valid section name, "all" for anything unknown."""
    return value if value in SECTIONS else "all"


def gather_today(session: Session, now: datetime | None = None) -> TodayData:
    """Collect the Today page's data as of `now`."""
    now = now or utcnow()
    zone = ZoneInfo(load_settings().timezone)
    local = now.astimezone(zone)  # "today" is the user's day, not the UTC day
    shortlist = gather_shortlist(session, MAX_TOP_JOBS, now, hide=reacted_job_ids(session))
    return TodayData(
        now,
        shortlist,
        *_follow_ups(session, now, zone),
        _deadlines(session, local.date()),
        local_now=local,
    )


def _follow_ups(session: Session, now: datetime, zone: ZoneInfo) -> tuple[list[FollowUp], int]:
    """Return the first few follow-ups due by the end of today, and how many more are overdue."""
    local = now.astimezone(zone)
    end_of_today = local.replace(hour=23, minute=59, second=59, microsecond=0).astimezone(UTC)
    rows = session.execute(
        select(Interaction, Person)
        .join(Person, Person.id == Interaction.person_id)
        .where(Interaction.follow_up_due.is_not(None), Interaction.follow_up_due <= end_of_today)
        .order_by(Interaction.follow_up_due, Interaction.id)
    ).all()
    shown = []
    for interaction, person in rows[:MAX_FOLLOW_UPS]:
        due = interaction.follow_up_due
        if due is None:  # the query keeps only follow-ups that are set
            continue
        company = person.company.name if person.company else None
        shown.append(
            FollowUp(person.full_name, company, interaction.summary, due, max(0, (now - due).days))
        )
    return shown, max(0, len(rows) - MAX_FOLLOW_UPS)


def _deadlines(session: Session, today: date) -> list[Deadline]:
    """Return deadlines in the next seven days (today included) among all ranked jobs.

    Not only the five shown cards: a job already reacted to, or ranked lower, still has a deadline
    worth seeing.
    """
    last = today + timedelta(days=6)
    found = []
    for job, _assessment in ranked_jobs(session, MAX_DEADLINE_SCAN):
        fields = (job.structured_fields or {}).get("fields") or {}
        try:
            when = date.fromisoformat(str(fields.get("deadline"))[:10])
        except ValueError:
            continue
        if today <= when <= last:
            found.append(Deadline(job.title, job.company.name, when, safe_link(job.url)))
    return sorted(found, key=lambda d: (d.when, d.title))
