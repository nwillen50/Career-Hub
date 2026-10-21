"""The cost log and the monthly budget.

Every call that reaches a provider is logged with its tokens and an estimated price. Cache hits
cost nothing and are not logged. Local models are free. When this month's cloud spend reaches the
budget, the router refuses calls that aren't marked `essential` (raises `BudgetExceeded`).
Prices are estimates from a table the caller supplies; they change, so they aren't hard-coded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.models import LLMUsage, RunLog, utcnow
from careerhub.llm.provider import Destination, Provider
from careerhub.llm.types import LLMResponse


class BudgetExceeded(Exception):
    """The month's model spending has reached the budget and the call isn't essential."""


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens for one model."""

    input_per_mtok: float
    output_per_mtok: float


def month_start(now: datetime) -> datetime:
    """Return midnight UTC on the first day of `now`'s month."""
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class CostLog:
    """Records usage and answers "how much this month?"."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        monthly_budget_usd: float,
        prices: Mapping[str, Price] | None = None,
        run_id: int | None = None,
    ) -> None:
        """Create a log.

        `prices` maps a provider name to its `Price`; a cloud provider with none logs $0 (and
        should be fixed). `run_id` ties the calls to a `run_log` row.
        """
        self._sessions = sessions
        self.monthly_budget_usd = monthly_budget_usd
        self._prices = dict(prices or {})
        self._run_id = run_id

    def estimate(self, provider: Provider, response: LLMResponse) -> float:
        """Estimated dollars for `response`: zero for local models or an unpriced provider."""
        price = self._prices.get(provider.name)
        if provider.destination is Destination.LOCAL or price is None:
            return 0.0
        return (
            response.input_tokens * price.input_per_mtok
            + response.output_tokens * price.output_per_mtok
        ) / 1_000_000

    def record(self, provider: Provider, response: LLMResponse) -> float:
        """Log one call and return its estimated cost. Also adds it to the run's `cost_usd`."""
        cost = self.estimate(provider, response)
        with self._sessions() as session:
            session.add(
                LLMUsage(
                    run_id=self._run_id,
                    provider=provider.name,
                    model=provider.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=cost,
                )
            )
            run = session.get(RunLog, self._run_id) if self._run_id is not None else None
            if run is not None:
                run.cost_usd += cost
            session.commit()
        return cost

    def spent_this_month(self, now: datetime | None = None) -> float:
        """Total estimated dollars logged since the start of the current UTC month."""
        start = month_start(now or utcnow())
        with self._sessions() as session:
            total = session.scalar(
                select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(
                    LLMUsage.created_at >= start
                )
            )
        return float(total or 0.0)

    def check_budget(self, provider: Provider, essential: bool) -> None:
        """Raise `BudgetExceeded` if a non-essential cloud call would run past the budget."""
        if essential or provider.destination is Destination.LOCAL:
            return
        if self.spent_this_month() >= self.monthly_budget_usd:
            raise BudgetExceeded(
                f"The monthly model budget (${self.monthly_budget_usd:.2f}) is reached; "
                f"{provider.name!r} calls that aren't essential are paused."
            )
