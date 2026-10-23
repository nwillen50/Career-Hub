"""The one morning notification (CH-147, PRD "Getting the user's attention").

After the nightly run: one notification saying how many new top jobs and follow-ups are waiting,
or that the run failed. At most one a day, and never a company, title, or person in its text.
Clicking it opens Today. The notification is a local macOS banner on the user's own Mac; nothing is
sent to anyone. (The email-to-self escalation stays off and is not built.)

`Notifier` is the seam: tests use `FakeNotifier`; the Mac uses `MacNotifier`, which asks macOS to
show a banner through `osascript`. Whether a banner can carry a click-through without a signed app
is the CH-33 spike, to be checked on a Mac.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from careerhub.core.files import write_private
from careerhub.core.paths import data_dir
from careerhub.modules.attention import AttentionState

STATE_FILENAME = "attention-notified.json"


@dataclass(frozen=True)
class Notification:
    """What to show: a title and a body, and the page that clicking opens."""

    title: str
    body: str
    opens: str = "Today"


class Notifier(Protocol):
    """Something that can show a notification."""

    def send(self, notification: Notification) -> None:
        """Show `notification` to the user."""


@dataclass
class FakeNotifier:
    """A notifier for tests: it records what it was asked to show."""

    sent: list[Notification] = field(default_factory=list)

    def send(self, notification: Notification) -> None:
        """Record `notification` instead of showing it."""
        self.sent.append(notification)


class MacNotifier:
    """Shows a banner through macOS's `osascript`. Only works on a Mac."""

    @staticmethod
    def command(notification: Notification) -> list[str]:
        """Return the command line for `notification` (quotes and backslashes are escaped)."""

        def quote(text: str) -> str:
            return text.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{quote(notification.body)}" '
            f'with title "{quote(notification.title)}"'
        )
        return ["osascript", "-e", script]

    def send(self, notification: Notification) -> None:
        """Show the banner (raises `RuntimeError` off a Mac)."""
        if sys.platform != "darwin":
            raise RuntimeError("Notifications can only be shown on a Mac.")
        done = subprocess.run(self.command(notification), check=False, timeout=30)
        if done.returncode != 0:  # not shown: the day must not be used up (see notify_once_a_day)
            raise RuntimeError(f"macOS did not show the notification (exit {done.returncode}).")


def message_for(state: AttentionState) -> Notification | None:
    """Return the morning message for `state`, or None when there is nothing to say."""
    if state.error:
        return Notification(
            "Career Hub", "The nightly run failed. Open Today to see what happened."
        )
    if state.count == 0:
        return None
    parts = []
    if state.new_top_jobs:
        parts.append(f"{state.new_top_jobs} new top job{'s' if state.new_top_jobs != 1 else ''}")
    if state.follow_ups_due:
        parts.append(
            f"{state.follow_ups_due} follow-up{'s' if state.follow_ups_due != 1 else ''} due"
        )
    return Notification("Career Hub", " and ".join(parts) + ". Open Today.")


def _state_path() -> Path:
    return data_dir() / STATE_FILENAME


def last_notified() -> date | None:
    """Return the day the last notification was shown, or None."""
    try:
        return date.fromisoformat(json.loads(_state_path().read_text(encoding="utf-8"))["day"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def notify_once_a_day(
    state: AttentionState, today: date, notifier: Notifier
) -> Notification | None:
    """Show the morning notification if there is something to say and none was shown today.

    Returns what was shown, or None. A failed send is not recorded, so the next try can show it.
    """
    if last_notified() == today:
        return None
    notification = message_for(state)
    if notification is None:
        return None
    notifier.send(notification)
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    write_private(_state_path(), json.dumps({"day": today.isoformat()}) + "\n")
    return notification
