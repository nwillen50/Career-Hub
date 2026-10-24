"""What the Browse tab shows (CH-187, part 1): every open job, searchable, sortable, and paged.

Reads only. The cards are the same ones Today shows, so a reaction here counts the same. Search is
a plain `LIKE` over the title, company, location, and the job summary: no model is called. The
page, sort, search text, and grouping live in the URL, so a view can be bookmarked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlencode

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, Company, Job, utcnow
from careerhub.modules.jobs.store import only_visible_copies
from careerhub.web.shortlist import build_entries
from careerhub.web.today import JobCard, job_cards

PAGE_SIZE = 25
MAX_SEARCH = 100
SORTS = {"score": "Best score", "newest": "Newest", "deadline": "Deadline", "company": "Company"}
DEFAULT_SORT = "score"


@dataclass(frozen=True)
class BrowseQuery:
    """The checked request: search text, sort, page (1-based), and grouping."""

    text: str = ""
    sort: str = DEFAULT_SORT
    page: int = 1
    group: bool = False

    @classmethod
    def clean(
        cls, text: str | None, sort: str | None, page: str | None, group: str | None
    ) -> BrowseQuery:
        """Build a query from raw URL values; anything unexpected becomes the default."""
        return cls(
            " ".join((text or "").split())[:MAX_SEARCH],
            sort if sort in SORTS else DEFAULT_SORT,
            int(page)
            if page and page.isascii() and page.isdigit() and 0 < int(page) < 10_000
            else 1,
            group == "place",
        )

    def params(self, **change: str | int | bool) -> str:
        """Return this query as a URL query string, with `change` applied (defaults left out)."""
        values: dict[str, str | int | bool] = {
            "q": self.text,
            "sort": self.sort,
            "page": self.page,
            "group": "place" if self.group else "",
            **change,
        }
        if values["sort"] == DEFAULT_SORT:
            values["sort"] = ""
        if values["page"] == 1:
            values["page"] = ""
        return urlencode({k: v for k, v in values.items() if v not in ("", False)})


@dataclass(frozen=True)
class BrowseView:
    """One page of results."""

    query: BrowseQuery
    cards: list[JobCard] = field(default_factory=list)
    total: int = 0
    pages: int = 1
    #: Set when the page asked for is past the last one.
    first_rank: int = 1

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


def _filtered(text: str) -> Select[tuple[Any, ...]]:
    """Return the query for open, original, scored jobs matching the search text."""
    summary = func.json_extract(Job.structured_fields, "$.fields.summary")
    query = (
        select(Job, Assessment)
        .join(Assessment, Assessment.job_id == Job.id)
        .join(Company, Company.id == Job.company_id)
        .where(
            Assessment.filter_passed.is_(True),
            Assessment.fit_score.is_not(None),
            Job.status != "closed",
        )
    )
    query = only_visible_copies(query)
    for word in text.split():  # every word must appear somewhere
        pattern = _like(word)
        query = query.where(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Company.name.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
                summary.ilike(pattern, escape="\\"),
            )
        )
    return cast("Select[tuple[Any, ...]]", query)


def gather_browse(session: Session, query: BrowseQuery, now: datetime | None = None) -> BrowseView:
    """Return the cards for one page of `query`, best first unless another sort is chosen."""
    now = now or utcnow()
    base = _filtered(query.text)
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
    cards = job_cards(build_entries(session, rows, now))
    return BrowseView(
        BrowseQuery(query.text, query.sort, page, query.group),
        cards,
        total,
        pages,
        (page - 1) * PAGE_SIZE + 1,
    )
