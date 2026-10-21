from __future__ import annotations

import pytest

from careerhub.core.settings import Settings


def test_timezone_and_calendar_defaults_and_validation() -> None:
    settings = Settings()
    assert settings.timezone == "America/New_York" and settings.calendar_files == ()
    assert (
        Settings(timezone="Europe/Paris", calendar_files=("/tmp/a.ics",)).timezone == "Europe/Paris"
    )
    with pytest.raises(ValueError, match="IANA"):
        Settings(timezone="Mars/Olympus")
