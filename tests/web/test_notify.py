"""The morning notification: wording, once a day, local only (CH-147)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from careerhub.cli import main
from careerhub.core.secrets import InMemoryKeyStore
from careerhub.modules.attention import AttentionState
from careerhub.modules.notify import (
    FakeNotifier,
    MacNotifier,
    Notification,
    last_notified,
    message_for,
    notify_once_a_day,
)

TODAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CAREERHUB_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_the_message_counts_jobs_and_follow_ups_and_names_no_one() -> None:
    assert message_for(AttentionState(2, 3, False)) == Notification(
        "Career Hub", "3 new top jobs and 2 follow-ups due. Open Today."
    )
    assert message_for(AttentionState(0, 1, False)).body == "1 new top job. Open Today."  # type: ignore[union-attr]
    assert message_for(AttentionState(1, 0, False)).body == "1 follow-up due. Open Today."  # type: ignore[union-attr]


def test_a_failed_run_says_so_and_nothing_to_say_sends_nothing() -> None:
    failed = message_for(AttentionState(5, 5, True))
    assert failed is not None and "nightly run failed" in failed.body
    assert message_for(AttentionState(0, 0, False)) is None


def test_at_most_one_notification_a_day() -> None:
    notifier = FakeNotifier()
    state = AttentionState(1, 1, False)
    assert notify_once_a_day(state, TODAY, notifier) is not None
    assert notify_once_a_day(state, TODAY, notifier) is None  # same day: silent
    assert len(notifier.sent) == 1 and last_notified() == TODAY
    assert notify_once_a_day(state, TODAY + timedelta(days=1), notifier) is not None
    assert len(notifier.sent) == 2


def test_nothing_to_say_does_not_use_up_the_day() -> None:
    notifier = FakeNotifier()
    assert notify_once_a_day(AttentionState(0, 0, False), TODAY, notifier) is None
    assert last_notified() is None
    assert notify_once_a_day(AttentionState(1, 0, False), TODAY, notifier) is not None


def test_a_failed_send_is_not_recorded_so_the_next_try_can_show_it() -> None:
    class Broken:
        def send(self, notification: Notification) -> None:
            raise RuntimeError("no display")

    with pytest.raises(RuntimeError):
        notify_once_a_day(AttentionState(1, 0, False), TODAY, Broken())
    assert last_notified() is None


def test_the_mac_command_escapes_quotes_and_is_a_single_osascript_call() -> None:
    command = MacNotifier.command(Notification('Say "hi"', 'back\\slash "quoted"'))
    assert command[:2] == ["osascript", "-e"] and len(command) == 3
    assert (
        command[2] == 'display notification "back\\\\slash \\"quoted\\"" with title "Say \\"hi\\""'
    )


def test_off_a_mac_the_banner_cannot_be_shown_and_the_command_says_so(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(RuntimeError, match="only be shown on a Mac"):
        MacNotifier().send(Notification("t", "b"))
    store = InMemoryKeyStore()
    assert main(["attention", "--notify"], store) == 0  # nothing pending: nothing to show
    assert "Nothing to show" in capsys.readouterr().out


def test_a_notification_macos_refuses_is_an_error_and_does_not_use_up_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review: osascript's exit code was ignored, so a banner that never showed used up the day."""
    import subprocess

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    with pytest.raises(RuntimeError, match="did not show"):
        notify_once_a_day(AttentionState(1, 0, False), TODAY, MacNotifier())
    assert last_notified() is None


def test_a_missing_osascript_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    monkeypatch.setattr("sys.platform", "darwin")

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(subprocess, "run", missing)
    monkeypatch.setattr(
        "careerhub.cli.attention_state", lambda session: AttentionState(1, 0, False)
    )
    assert main(["attention", "--notify"], InMemoryKeyStore()) == 1
    assert "Could not show the notification" in capsys.readouterr().out
