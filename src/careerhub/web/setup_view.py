"""What the Get started page shows: each setup step with a live done / not done check (CH-203).

Read-only. It reuses the System status data and the readiness checks, then adds the few checks they
lack (input files, profile files, followed companies). Nothing here shows a key, a file's text, or
a posting: only yes/no answers, counts, and the exact command or file that fixes each step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerhub.core.models import RunLog, Source
from careerhub.core.secrets import KeyStore
from careerhub.llm.catalog import ollama_is_running
from careerhub.modules.jobs.targets import TargetsError, load_targets
from careerhub.profile.facts import counts
from careerhub.profile.files import ProfileFileError, load_preferences, load_profile_doc
from careerhub.profile.inputs import find_resume, find_transcript
from careerhub.profile.linkedin import connections_path
from careerhub.web.status_view import gather_status

#: Steps 1 to this number must be done before the page stops opening first.
REQUIRED_STEPS = 7


@dataclass(frozen=True)
class SetupStep:
    """One setup step: whether it is done, what is missing, and how to fix it."""

    number: int
    title: str
    done: bool
    detail: str
    fix: str
    #: A page of the app that helps with this step, as (path, label), or None.
    link: tuple[str, str] | None = None


@dataclass(frozen=True)
class OptionalStep:
    """A nice-to-have step; it is not checked."""

    title: str
    fix: str


@dataclass(frozen=True)
class SetupView:
    """The whole checklist."""

    steps: list[SetupStep] = field(default_factory=list)
    optional: list[OptionalStep] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True when every required step (1 to 7) is done."""
        return all(s.done for s in self.steps if s.number <= REQUIRED_STEPS)

    @property
    def next_step(self) -> SetupStep | None:
        """The first step that is not done, if any."""
        return next((s for s in self.steps if not s.done), None)


OPTIONAL = [
    OptionalStep(
        "Apple Calendar for the weekly plan", "allow calendar access when `hub plan` asks."
    ),
    OptionalStep("Alumni lists", "`hub people import-alumni <file>`."),
    OptionalStep("Run every night by itself", "`hub schedule install`, then the printed lines."),
]


def gather_setup(
    session: Session,
    store: KeyStore | None = None,
    local_running: Callable[[], bool] = ollama_is_running,
) -> SetupView:
    """Check every setup step as of now. Any one check failing means "not done", never an error."""
    status = gather_status(session, store, local_running=local_running)
    steps = [
        SetupStep(1, "Database key in the Keychain", True,
                  "The encrypted database opened, so its key is in place.",
                  "Nothing to do."),
    ]  # fmt: skip
    with_model = [task for task, route, _ in status.routes if route != "(no route)"]
    steps.append(
        SetupStep(
            2,
            "A model key, and a model for each task",
            bool(status.models_on) and not status.models_problem,
            status.models_problem
            or f"{len(with_model)} of {len(status.routes)} tasks have a model.",
            "`hub models add anthropic` (or `gemini`, `openai`, `local`), then `hub models check`.",
            ("/status", "System status"),
        )
    )
    resume, _ = find_resume()
    transcript, _ = find_transcript()
    linkedin = connections_path().exists()
    seen = ", ".join(
        f"{label} {'found' if found else 'not found'}"
        for label, found in (("resume", resume), ("transcript", transcript), ("LinkedIn", linkedin))
    )
    steps.append(
        SetupStep(3, "Input files", resume is not None, f"{seen}. The resume is required.",
                  "Put your files in `inputs/resume`, `inputs/transcript`, `inputs/linkedin` "
                  "in the data folder.")
    )  # fmt: skip
    problem = ""
    try:
        has_files = load_profile_doc() is not None and load_preferences() is not None
        if not has_files:
            problem = "profile.md or preferences.yaml is missing."
    except ProfileFileError:
        has_files, problem = False, "profile.md or preferences.yaml has a problem."
    waiting = counts(session)["pending"]
    steps.append(
        SetupStep(
            4, "Profile files, and resume facts reviewed",
            has_files and waiting == 0,
            problem or (f"{waiting} facts are waiting for your review." if waiting else
                        "Both files are present and valid."),
            "Create `profile/profile.md` and `profile/preferences.yaml` (see the README), then "
            "accept or reject the facts on the Profile tab.",
            ("/profile", "Profile"),
        )
    )  # fmt: skip
    steps.append(
        SetupStep(
            5, "Scoring switched on", status.scoring_on,
            f"On since {status.scoring_since}." if status.scoring_on
            else (status.missing[0] if status.missing else "Run the command to switch it on."),
            "`hub profile ready`", ("/profile", "Profile"),
        )
    )  # fmt: skip
    try:
        followed = sum(1 for t in load_targets() if t.enabled)
    except TargetsError:
        followed = 0
    approved = session.scalar(
        select(func.count()).select_from(Source).where(Source.status == "approved",
                                                       Source.kind != "manual")
    ) or 0  # fmt: skip
    steps.append(
        SetupStep(
            6, "Companies you follow, and an approved job source", followed > 0 and approved > 0,
            f"{followed} companies followed; {approved} approved sources.",
            "`hub targets import docs/seeds/engine-resident-companies.yaml`, "
            "`hub targets enable-matching`, `hub targets find-boards`, then "
            "`hub sources approve <id>`.",
            ("/status", "System status"),
        )
    )  # fmt: skip
    nights = session.scalar(
        select(func.count()).select_from(RunLog).where(RunLog.step == "nightly",
                                                       RunLog.status == "succeeded")
    ) or 0  # fmt: skip
    steps.append(
        SetupStep(7, "A first nightly run", nights > 0,
                  "A nightly run has finished." if nights else "No nightly run has finished yet.",
                  "`hub nightly`")
    )  # fmt: skip
    return SetupView(steps, OPTIONAL)
