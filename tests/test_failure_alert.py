"""A failed nightly run reaches the user as one banner a day (CH-164 part 2, CH-173)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from test_pipeline import NOW, SETTINGS, env, handler  # noqa: F401  (env is a fixture)

from careerhub.core.models import RunLog
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.core.storage import session_factory
from careerhub.modules.attention import attention_state
from careerhub.modules.notify import FakeNotifier, notify_once_a_day
from careerhub.pipeline import run_nightly

TODAY = date(2026, 10, 21)


def test_every_source_failing_after_retries_shows_one_banner_and_only_one(env: Any) -> None:  # noqa: F811
    engine, _ = env
    report = run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(board_status=503), NOW)
    assert report.sources_failed == 1 and any("run failed" in p for p in report.problems)
    notifier = FakeNotifier()
    with session_factory(engine)() as session:
        state = attention_state(session)
        assert state.error is True
        shown = notify_once_a_day(state, TODAY, notifier)
        assert shown is not None and "nightly run failed" in shown.body
        assert notify_once_a_day(state, TODAY, notifier) is None
    assert len(notifier.sent) == 1
    assert "Acme" not in notifier.sent[0].body  # the banner names no company or job


def test_a_permanent_error_also_raises_the_alert(env: Any) -> None:  # noqa: F811
    engine, _ = env
    run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(board_status=404), NOW)
    with session_factory(engine)() as session:
        assert attention_state(session).error is True


def test_the_next_good_run_clears_the_alert_and_stays_silent(env: Any) -> None:  # noqa: F811
    engine, _ = env
    store = InMemoryKeyStore()
    run_nightly(engine, store, SETTINGS, handler(board_status=503), NOW)
    run_nightly(engine, store, SETTINGS, handler(), NOW + timedelta(days=1))
    notifier = FakeNotifier()
    with session_factory(engine)() as session:
        runs = session.scalars(select(RunLog).order_by(RunLog.id)).all()
        assert [r.status for r in runs] == ["failed", "succeeded"]
        state = attention_state(session)
        assert state.error is False
        shown = notify_once_a_day(state, TODAY + timedelta(days=1), notifier)
        assert shown is None or "failed" not in shown.body


def test_a_run_that_was_cut_off_is_closed_and_counts_as_a_failure_until_the_next_good_run(
    env: Any,  # noqa: F811
) -> None:
    engine, _ = env
    with session_factory(engine)() as session:
        from careerhub.core.runlog import start_run

        start_run(session, "nightly")  # the Mac lost power: this row stays 'running'
    run_nightly(engine, InMemoryKeyStore(), SETTINGS, handler(), NOW)
    with session_factory(engine)() as session:
        first, second = session.scalars(select(RunLog).order_by(RunLog.id)).all()
        assert (first.status, first.error_summary) == ("failed", "interrupted")
        assert second.status == "succeeded"
        assert attention_state(session).error is False
