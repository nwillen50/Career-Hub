"""What the System status tab shows (CH-144): runs, sources, spending, models, readiness, waiting.

Read-only. Nothing here shows a key, a prompt, a profile detail, or a posting: only names, counts,
times, dollars, and error type names that the run log already keeps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.companies import pending_merges
from careerhub.core.models import LLMUsage, RunLog, Source, utcnow
from careerhub.core.secrets import KeychainKeyStore, KeyStore
from careerhub.core.settings import load_settings
from careerhub.llm.catalog import PERSONAL_TASKS, PUBLIC_TASKS, ollama_is_running, with_catalog
from careerhub.llm.cost import month_start
from careerhub.profile.readiness import current_readiness

MAX_RUNS = 8


@dataclass(frozen=True)
class RunView:
    """One run of a pipeline step."""

    step: str
    status: str
    started: str
    items: int
    cost: str
    problem: str | None


@dataclass(frozen=True)
class SourceView:
    """One job source and how its last fetch went."""

    name: str
    kind: str
    status: str
    health: str
    last_fetch: str | None


@dataclass(frozen=True)
class StatusView:
    """Everything the System status tab shows."""

    runs: list[RunView] = field(default_factory=list)
    sources: list[SourceView] = field(default_factory=list)
    spent: float = 0.0
    budget: float = 0.0
    models_on: list[str] = field(default_factory=list)
    routes: list[tuple[str, str, str]] = field(default_factory=list)  # task, route, why
    models_problem: str | None = None
    scoring_on: bool = False
    scoring_since: str | None = None
    missing: list[str] = field(default_factory=list)
    proposed_sources: int = 0
    merge_suggestions: int = 0

    @property
    def spend_text(self) -> str:
        """Describe spending against the monthly budget."""
        return f"${self.spent:,.2f} of ${self.budget:,.2f} this month"


def _stamp(moment: datetime | None) -> str | None:
    return f"{moment:%a %b %d, %H:%M} UTC" if moment else None


def gather_status(
    session: Session,
    store: KeyStore | None = None,
    now: datetime | None = None,
    local_running: Callable[[], bool] = ollama_is_running,
) -> StatusView:
    """Collect the status of the whole system as of `now`."""
    now = now or utcnow()
    settings = load_settings()
    runs = [
        RunView(
            r.step,
            r.status,
            _stamp(r.started_at) or "",
            r.items_processed,
            f"${r.cost_usd:,.2f}",
            r.error_summary,
        )
        for r in session.scalars(
            select(RunLog).order_by(RunLog.started_at.desc(), RunLog.id.desc()).limit(MAX_RUNS)
        )
    ]
    sources = [
        SourceView(s.name, s.kind, s.status, s.health, _stamp(s.last_fetched_at))
        for s in session.scalars(select(Source).order_by(Source.id))
        if s.kind != "manual"
    ]
    spent = session.scalar(
        select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(
            LLMUsage.created_at >= month_start(now)
        )
    )
    models_on: list[str] = []
    routes: list[tuple[str, str, str]] = []
    models_problem: str | None = None
    try:
        setup = with_catalog(settings, store or KeychainKeyStore(), local_running)
        models_on = [p.name for p in setup.settings.providers]
        for task in (*PUBLIC_TASKS, *PERSONAL_TASKS):
            route = " -> ".join(setup.settings.model_routes.get(task, ())) or "(no route)"
            routes.append((task, route, setup.reasons.get(task, "")))
    except Exception as error:  # the Keychain or settings may be unavailable; say so, don't fail
        models_problem = f"Models could not be read ({type(error).__name__})."
    readiness = current_readiness(session, settings.profile_min_skills)
    return StatusView(
        runs=runs,
        sources=sources,
        spent=float(spent or 0.0),
        budget=settings.monthly_budget_usd,
        scoring_on=readiness.on,
        scoring_since=str(readiness.since) if readiness.since else None,
        missing=list(readiness.missing),
        proposed_sources=sum(1 for s in sources if s.status == "proposed"),
        merge_suggestions=len(pending_merges(session)),
        models_on=models_on,
        routes=routes,
        models_problem=models_problem,
    )
