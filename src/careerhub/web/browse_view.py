"""What the Browse tab shows (CH-187, part 1): every open job, searchable, sortable, and paged.

Reads only. The cards are the same ones Today shows, so a reaction here counts the same. Search is
a plain `LIKE` over the title, company, location, and the job summary: no model is called. The
page, sort, search text, and grouping live in the URL, so a view can be bookmarked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Company, Feedback, Job, Source, utcnow
from careerhub.modules.jobs.store import only_visible_copies
from careerhub.web.shortlist import build_entries
from careerhub.web.today import JobCard, job_cards

PAGE_SIZE = 25
MAX_SEARCH = 100
SORTS = {"score": "Best score", "newest": "Newest", "deadline": "Deadline", "company": "Company"}
DEFAULT_SORT = "score"


MODES = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "unknown": "Not stated"}
AGES = {"1": "Today", "7": "Past week", "30": "Past month"}
REACTED = {"hide": "Hide jobs I reacted to", "only": "Only jobs I reacted to"}


def _score(value: str | None) -> int | None:
    """Read a score bound (0 to 100) from the URL; anything else means no bound."""
    ok = value and value.isascii() and value.isdigit() and int(value) <= 100
    return int(value) if value and ok else None


@dataclass(frozen=True)
class BrowseQuery:
    """The checked request: search text, filters, sort, page (1-based), and grouping."""

    text: str = ""
    sort: str = DEFAULT_SORT
    page: int = 1
    group: bool = False
    min_score: int | None = None
    max_score: int | None = None
    company: str = ""
    place: str = ""
    mode: str = ""
    age: str = ""
    source: int | None = None
    reacted: str = ""
    #: True to list the jobs the hard filters removed, each with its reason.
    filtered: bool = False

    @classmethod
    def clean(cls, raw: Mapping[str, str | None]) -> BrowseQuery:
        """Build a query from raw URL values; anything unexpected becomes the default."""
        page, source = raw.get("page"), raw.get("source")

        def text(key: str) -> str:
            return " ".join((raw.get(key) or "").split())[:MAX_SEARCH]

        return cls(
            text("q"),
            raw.get("sort") if raw.get("sort") in SORTS else DEFAULT_SORT,  # type: ignore[arg-type]
            int(page)
            if page and page.isascii() and page.isdigit() and 0 < int(page) < 10_000
            else 1,
            raw.get("group") == "place",
            _score(raw.get("min_score")),
            _score(raw.get("max_score")),
            text("company"),
            text("place"),
            raw.get("mode") if raw.get("mode") in MODES else "",  # type: ignore[arg-type]
            raw.get("age") if raw.get("age") in AGES else "",  # type: ignore[arg-type]
            int(source)
            if source and source.isascii() and source.isdigit() and int(source) < 2**62
            else None,
            raw.get("reacted") if raw.get("reacted") in REACTED else "",  # type: ignore[arg-type]
            raw.get("filtered") == "1",
        )

    def params(self, **change: str | int | bool | None) -> str:
        """Return this query as a URL query string, with `change` applied (defaults left out)."""
        values: dict[str, Any] = {
            "q": self.text,
            "sort": "" if self.sort == DEFAULT_SORT else self.sort,
            "page": "" if self.page == 1 else self.page,
            "group": "place" if self.group else "",
            "min_score": self.min_score,
            "max_score": self.max_score,
            "company": self.company,
            "place": self.place,
            "mode": self.mode,
            "age": self.age,
            "source": self.source,
            "reacted": self.reacted,
            "filtered": "1" if self.filtered else "",
            **change,
        }
        if values["page"] == 1:
            values["page"] = ""
        return urlencode({k: v for k, v in values.items() if v not in ("", False, None)})

    @property
    def has_filters(self) -> bool:
        """True when any filter (not the search, sort, or paging) is set."""
        return any(
            (self.min_score is not None, self.max_score is not None, self.company, self.place,
             self.mode, self.age, self.source, self.reacted, self.filtered)
        )  # fmt: skip


@dataclass(frozen=True)
class FilteredRow:
    """A job the hard filters removed, with the reasons."""

    job_id: int
    title: str
    company: str
    location: str
    reasons: list[str]


@dataclass(frozen=True)
class BrowseView:
    """One page of results."""

    query: BrowseQuery
    cards: list[JobCard] = field(default_factory=list)
    total: int = 0
    pages: int = 1
    #: The rank number of the first card on this page.
    first_rank: int = 1
    filtered_rows: list[FilteredRow] = field(default_factory=list)
    sources: list[tuple[int, str]] = field(default_factory=list)

    @property
    def has_previous(self) -> bool:
        """True when there is an earlier page."""
        return self.query.page > 1

    @property
    def has_next(self) -> bool:
        """True when there is a later page."""
        return self.query.page < self.pages


def _like(text: str) -> str:
    """Return `text` as a LIKE pattern that matches it literally anywhere."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filtered(q: BrowseQuery, now: datetime) -> Select[tuple[Any, ...]]:
    """Return the query for open, original jobs matching the search text and the filters."""
    summary = func.json_extract(Job.structured_fields, "$.fields.summary")
    mode = func.json_extract(Job.structured_fields, "$.fields.remote")
    query = (
        select(Job, Assessment)
        .join(Assessment, Assessment.job_id == Job.id)
        .join(Company, Company.id == Job.company_id)
        .where(Job.status != "closed")
    )
    if q.filtered:  # the jobs the hard filters removed
        query = query.where(Assessment.filter_passed.is_(False))
    else:
        query = query.where(Assessment.filter_passed.is_(True), Assessment.fit_score.is_not(None))
    query = only_visible_copies(query)
    for word in q.text.split():  # every word must appear somewhere
        pattern = _like(word)
        query = query.where(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Company.name.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
                summary.ilike(pattern, escape="\\"),
            )
        )
    if q.min_score is not None:
        query = query.where(Assessment.fit_score >= q.min_score)
    if q.max_score is not None:
        query = query.where(Assessment.fit_score <= q.max_score)
    if q.company:
        query = query.where(Company.name.ilike(_like(q.company), escape="\\"))
    if q.place:
        query = query.where(Job.location.ilike(_like(q.place), escape="\\"))
    if q.mode == "unknown":
        query = query.where(or_(mode.is_(None), mode == "unknown"))
    elif q.mode:
        query = query.where(mode == q.mode)
    if q.age:
        query = query.where(Job.first_seen_at >= now - timedelta(days=int(q.age)))
    if q.source is not None:
        query = query.where(Job.source_id == q.source)
    if q.reacted:
        seen = select(Feedback.job_id)
        query = query.where(Job.id.not_in(seen) if q.reacted == "hide" else Job.id.in_(seen))
    return cast("Select[tuple[Any, ...]]", query)


def gather_browse(session: Session, query: BrowseQuery, now: datetime | None = None) -> BrowseView:
    """Return the cards for one page of `query`, best first unless another sort is chosen."""
    now = now or utcnow()
    base = _filtered(query, now)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(query.page, pages)
    deadline = func.json_extract(Job.structured_fields, "$.fields.deadline")
    order: tuple[Any, ...] = {
        "score": (Assessment.fit_score.desc(), Job.id),
        "newest": (Job.first_seen_at.desc(), Job.id),
        "deadline": (deadline.is_(None), deadline, Assessment.fit_score.desc(), Job.id),
        "company": (func.lower(Company.name), Assessment.fit_score.desc(), Job.id),
    }[query.sort]
    page_query = base.order_by(*order).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    rows = [(row[0], row[1]) for row in session.execute(page_query)]
    sources = [(row[0], row[1]) for row in session.execute(select(Source.id, Source.name))]
    shown = replace(query, page=page)
    first = (page - 1) * PAGE_SIZE + 1
    if query.filtered:  # no score, no buttons: these jobs were removed by your own rules
        removed = [
            FilteredRow(
                job.id, job.title, job.company.name, job.location or "",
                [str(r) for r in (assessment.filter_results or {}).get("reasons", [])],
            )
            for job, assessment in rows
        ]  # fmt: skip
        return BrowseView(shown, [], total, pages, first, removed, sources)
    cards = job_cards(build_entries(session, rows, now))
    return BrowseView(shown, cards, total, pages, first, [], sources)
