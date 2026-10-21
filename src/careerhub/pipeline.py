"""The nightly run: ingest, understand, prioritize, write the shortlist, back up (steps 4 to 6).

`run_nightly` does each stage for everything that needs it and keeps going when one piece fails:

- a source that can't be fetched is recorded and the others still run;
- a job whose extraction or scoring fails is counted and skipped;
- when the monthly budget is reached, model work stops but the page is still written;
- missing profile files or model routes skip the stages that need them and say so.

The run is recorded in `run_log` (step `nightly`). It is marked **failed**, which the shortlist page
shows, only when nothing useful could be done: every approved source failed, or the page could
not be written. Smaller problems are returned in `NightlyReport.problems` (type names and counts,
never posting or profile text). The run only reads public feeds and writes local files; it never
sends or submits anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from careerhub.core.backup import backup_database
from careerhub.core.models import Job, utcnow
from careerhub.core.runlog import finish_run, start_run
from careerhub.core.secrets import KeyStore
from careerhub.core.settings import Settings, load_settings
from careerhub.core.storage import session_factory
from careerhub.llm import BudgetExceeded, PrivacyViolation
from careerhub.llm.providers import ProviderError
from careerhub.llm.setup import NoRoute, RouterSetup, build_router, routes_for
from careerhub.llm.validated import ValidationFailed
from careerhub.modules.jobs import (
    FetchError,
    SourceNotApproved,
    approved_sources,
    ingest_greenhouse,
)
from careerhub.modules.jobs.extract import extract_and_save
from careerhub.modules.jobs.filters import assess_filters
from careerhub.modules.jobs.scoring import ScoringError, ranked_jobs, score_job
from careerhub.modules.jobs.store import only_visible_copies
from careerhub.modules.jobs.tailoring import tailor_job
from careerhub.profile.files import load_preferences, load_profile_doc
from careerhub.profile.inputs import find_resume
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.rubric import load_rubric
from careerhub.web.shortlist import MAX_JOBS, write_shortlist


@dataclass
class NightlyReport:
    """What a nightly run did. `problems` holds one short line per thing that went wrong."""

    sources_ok: int = 0
    sources_failed: int = 0
    new_jobs: int = 0
    extracted: int = 0
    filtered_out: int = 0
    scored: int = 0
    tailored: int = 0
    problems: list[str] = field(default_factory=list)
    shortlist_path: str | None = None

    def summary(self) -> str:
        """Render the report as plain text."""
        lines = [
            f"Sources: {self.sources_ok} fetched, {self.sources_failed} failed. "
            f"{self.new_jobs} new jobs.",
            f"Understood {self.extracted}, filtered out {self.filtered_out}, scored {self.scored}, "
            f"tailored {self.tailored}.",
        ]
        if self.shortlist_path:
            lines.append(f"Shortlist: {self.shortlist_path}")
        lines += [f"Problem: {problem}" for problem in self.problems]
        return "\n".join(lines)


def run_nightly(
    engine: Engine,
    store: KeyStore,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> NightlyReport:
    """Run the whole pipeline once and record it.

    Returns the report; a problem in one source or job never raises.
    """
    settings = settings or load_settings()
    sessions = session_factory(engine)
    report = NightlyReport()
    with sessions() as session:
        run = start_run(session, "nightly")
        error: BaseException | None = None
        try:
            _run(session, engine, store, settings, client, report, run.id)
            if report.sources_failed and not report.sources_ok:
                raise FetchError("every approved source failed")
        except Exception as caught:
            error = caught
            report.problems.append(f"run failed: {type(caught).__name__}")
        finish_run(session, run, items_processed=report.scored, error=error)
        # The page and backup come after the run is recorded, so the page shows this run's
        # outcome (and its finish time) and the backup includes the run log.
        try:
            report.shortlist_path = str(write_shortlist(session, now or utcnow()))
        except OSError as caught:
            report.problems.append(
                f"run failed: the shortlist page could not be written ({caught.errno})"
            )
        _backup(engine, settings, report)
    return report


def _run(
    session: Session,
    engine: Engine,
    store: KeyStore,
    settings: Settings,
    client: httpx.Client | None,
    report: NightlyReport,
    run_id: int,
) -> None:
    sessions = session_factory(engine)
    setup = build_router(settings, store, sessions, client, run_id)
    report.problems += [f"provider: {p}" for p in setup.problems]
    for source in approved_sources(session, "greenhouse"):
        try:
            result = ingest_greenhouse(session, source, client)
            report.sources_ok += 1
            report.new_jobs += result.new
        except (FetchError, SourceNotApproved) as caught:
            report.sources_failed += 1
            report.problems.append(f"source {source.id} failed: {type(caught).__name__}")
    jobs = list(
        session.scalars(
            only_visible_copies(select(Job).where(Job.status != "closed")).order_by(Job.id)
        )
    )
    _understand_and_rank(session, setup, settings, jobs, report)


def _backup(engine: Engine, settings: Settings, report: NightlyReport) -> None:
    try:
        backup_database(engine, keep=settings.backup_keep)
    except Exception as caught:  # a failed backup must not hide the rest of the run
        report.problems.append(f"backup failed: {type(caught).__name__}")


def _understand_and_rank(
    session: Session,
    setup: RouterSetup,
    settings: Settings,
    jobs: list[Job],
    report: NightlyReport,
) -> None:
    doc, rubric = load_profile_doc(), load_rubric()
    preferences = load_preferences()
    constraints = preferences.hard_constraints if preferences else HardConstraints()
    if doc is None or rubric is None:
        report.problems.append("scoring skipped: profile.md or rubric.yaml is missing")
    try:
        extract_routes = routes_for(settings, "extract", setup.built)
    except NoRoute:
        extract_routes = []
        report.problems.append("understanding skipped: no usable model for 'extract'")
    try:
        score_routes = routes_for(settings, "score", setup.built)
    except NoRoute:
        score_routes = []
        report.problems.append("scoring skipped: no usable model for 'score'")
    budget_hit = False
    failures = 0
    for job in jobs:
        try:
            if (
                extract_routes
                and not budget_hit
                and extract_and_save(session, setup.router, job, extract_routes)
            ):
                report.extracted += 1
            assessment = assess_filters(session, job, constraints)
            if not assessment.filter_passed:
                report.filtered_out += 1
            elif (
                doc
                and rubric
                and score_routes
                and not budget_hit
                and score_job(session, setup.router, job, doc, rubric, score_routes)
            ):
                report.scored += 1
        except BudgetExceeded:
            budget_hit = True
            report.problems.append("monthly budget reached: model work paused")
        except PrivacyViolation:
            report.problems.append("a provider is not allowed to see this data; check model_routes")
            return
        except (ValidationFailed, ScoringError, ProviderError):
            failures += 1
    if failures:
        report.problems.append(f"{failures} jobs could not be understood or scored")
    if doc is not None and not budget_hit:
        _tailor_top_jobs(session, setup, settings, doc, report)


def _tailor_top_jobs(
    session: Session,
    setup: RouterSetup,
    settings: Settings,
    doc: ProfileDoc,
    report: NightlyReport,
) -> None:
    """Write tailoring notes for the shortlist's jobs, if a 'tailor' route and a resume exist.

    Optional: with no 'tailor' route configured this does nothing and says nothing.
    """
    try:
        routes = routes_for(settings, "tailor", setup.built)
    except NoRoute:
        return
    resume, _ = find_resume()
    if resume is None:
        report.problems.append("tailoring skipped: no LaTeX resume found in inputs/resume")
        return
    source = resume.text()
    failures = 0
    for job, _assessment in ranked_jobs(session, MAX_JOBS):
        try:
            if tailor_job(session, setup.router, job, doc, source, routes):
                report.tailored += 1
        except BudgetExceeded:
            report.problems.append("monthly budget reached: tailoring paused")
            return
        except PrivacyViolation:
            report.problems.append(
                "tailoring skipped: a 'tailor' provider may not see Personal data"
            )
            return
        except (ValidationFailed, ProviderError):
            failures += 1
    if failures:
        report.problems.append(f"{failures} jobs could not be tailored")
