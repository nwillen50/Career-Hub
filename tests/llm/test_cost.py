from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from careerhub.core.migrate import migrate
from careerhub.core.models import LLMUsage, RunLog, utcnow
from careerhub.core.runlog import start_run
from careerhub.core.secrets import new_database_key
from careerhub.core.settings import Settings
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import (
    BudgetExceeded,
    CostLog,
    Destination,
    LLMRequest,
    Price,
    PrivacyTier,
    Router,
)
from careerhub.llm.cache import ResponseCache
from careerhub.llm.cost import month_start
from careerhub.llm.fake import FakeProvider

PRICES = {"cloud": Price(input_per_mtok=1.0, output_per_mtok=5.0)}


@pytest.fixture
def sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)


def cloud() -> FakeProvider:
    return FakeProvider("cloud", Destination.CLOUD, input_tokens=1_000_000, output_tokens=200_000)


def ask(prompt: str = "p", essential: bool = False) -> LLMRequest:
    return LLMRequest(prompt=prompt, tier=PrivacyTier.PUBLIC, essential=essential)


def test_cost_is_estimated_from_tokens_and_logged(sessions: sessionmaker[Session]) -> None:
    costs = CostLog(sessions, 100.0, PRICES)
    Router([cloud()], costs=costs).complete(ask(), "cloud")
    assert costs.spent_this_month() == pytest.approx(2.0)  # $1 input + $1 output
    with sessions() as session:
        assert session.query(LLMUsage).one().cost_usd == pytest.approx(2.0)


def test_local_models_are_free(sessions: sessionmaker[Session]) -> None:
    local = FakeProvider("cloud", Destination.LOCAL, input_tokens=5_000_000)
    costs = CostLog(sessions, 100.0, PRICES)
    Router([local], costs=costs).complete(ask(), "cloud")
    assert costs.spent_this_month() == 0.0


def test_budget_pauses_non_essential_cloud_calls(sessions: sessionmaker[Session]) -> None:
    provider = cloud()
    router = Router([provider], costs=CostLog(sessions, 2.0, PRICES))
    router.complete(ask("one"), "cloud")  # spends $2: budget reached
    with pytest.raises(BudgetExceeded):
        router.complete(ask("two"), "cloud")
    assert len(provider.requests) == 1


def test_essential_calls_and_local_models_continue_over_budget_up_to_a_ceiling(
    sessions: sessionmaker[Session],
) -> None:
    local = FakeProvider("local")
    router = Router([cloud(), local], costs=CostLog(sessions, 2.0, PRICES))
    router.complete(ask("a", essential=True), "cloud")  # $2: the budget is reached
    router.complete(ask("b", essential=True), "cloud")  # essential: allowed up to 2x ($4)
    router.complete(ask("c"), "local")  # local is always free
    with pytest.raises(BudgetExceeded, match="essential"):
        router.complete(ask("d", essential=True), "cloud")  # $4 spent: the ceiling


def test_cache_hits_are_free_and_allowed_over_budget(sessions: sessionmaker[Session]) -> None:
    provider = cloud()
    costs = CostLog(sessions, 2.0, PRICES)
    router = Router([provider], cache=ResponseCache(sessions), costs=costs)
    router.complete(ask(), "cloud")
    again = router.complete(ask(), "cloud")  # over budget now, but answered from the cache
    assert again.cached and costs.spent_this_month() == pytest.approx(2.0)


def test_only_this_months_spending_counts(sessions: sessionmaker[Session]) -> None:
    costs = CostLog(sessions, 100.0, PRICES)
    Router([cloud()], costs=costs).complete(ask(), "cloud")
    later = datetime(2099, 1, 15, tzinfo=UTC)
    assert costs.spent_this_month(later) == 0.0
    assert month_start(later) == datetime(2099, 1, 1, tzinfo=UTC)


def test_cost_is_added_to_the_run(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        run_id = start_run(session, "understand").id
    costs = CostLog(sessions, 100.0, PRICES, run_id=run_id)
    Router([cloud()], costs=costs).complete(ask(), "cloud")
    with sessions() as session:
        assert session.get(RunLog, run_id).cost_usd == pytest.approx(2.0)  # type: ignore[union-attr]


def test_budget_setting_defaults_and_rejects_negative() -> None:
    assert Settings().monthly_budget_usd == 20.0
    with pytest.raises(ValueError):
        Settings(monthly_budget_usd=-1)


def test_utcnow_is_what_the_log_uses() -> None:
    assert utcnow().tzinfo is not None
