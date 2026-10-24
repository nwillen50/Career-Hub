"""`hub web` hands the app its model openers, so the Conversation panel can answer (bug CH-202)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore


def test_hub_web_passes_the_router_opener(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path))  # never read the real data folder
    seen: dict[str, Any] = {}

    def fake_create_app(*args: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "app"

    monkeypatch.setattr("careerhub.web.app.create_app", fake_create_app)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["web"], InMemoryKeyStore()) == 0
    assert callable(seen.get("open_router")), "Conversation would always say 'No model is set up'"
    assert callable(seen.get("open_session"))
