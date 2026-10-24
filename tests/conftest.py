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


@pytest.fixture(autouse=True)
def never_the_real_keychain(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> object:
    """Fail any test that reaches the real Keychain (CH-206).

    On a Mac a stray call opens a system dialog (once it even offered to reset the login keychain).
    Tests use InMemoryKeyStore or a fake backend; only test_secrets_real_keychain.py, turned on by
    CAREERHUB_TEST_REAL_KEYCHAIN=1, may use the real one.
    """
    import types

    import keyring.errors

    if request.node.path.name == "test_secrets_real_keychain.py":
        yield None
        return
    touched: list[str] = []

    def refuse(action: str):  # type: ignore[no-untyped-def]
        def call(*args: object, **kwargs: object) -> None:
            touched.append(action)
            raise keyring.errors.NoKeyringError("tests must not use the real Keychain")

        return call

    fake = types.SimpleNamespace(
        get_password=refuse("get"),
        set_password=refuse("set"),
        delete_password=refuse("delete"),
        errors=keyring.errors,
    )
    monkeypatch.setattr("careerhub.core.secrets.keyring", fake)
    yield None
    if touched:
        pytest.fail(f"this test reached the real Keychain ({', '.join(touched)}); use a test store")


@pytest.fixture(autouse=True)
def never_the_real_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The weekly plan never opens the real Apple Calendar in a test (CH-208).

    On a Mac with EventKit installed, tests that turn Apple Calendar on used to read the user's
    real calendar (and could ask for permission). Tests that need calendar data pass a fake store
    to AppleCalendar; here the plan sees Apple Calendar as unavailable, as it is off a Mac.
    """
    from careerhub.modules.plan.apple import CalendarUnavailable

    def unavailable() -> object:
        raise CalendarUnavailable("Apple Calendar is never read in tests.")

    monkeypatch.setattr("careerhub.modules.plan.suggest.open_event_kit_store", unavailable)
