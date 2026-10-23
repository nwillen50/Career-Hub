"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_real_waiting_between_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source retry (CH-164) pauses between tries; tests must never really wait."""
    import types

    monkeypatch.setattr(
        "careerhub.modules.jobs.ingest.time", types.SimpleNamespace(sleep=lambda seconds: None)
    )
