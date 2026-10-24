"""`hub demo`: try the web app with made-up data (CH-192).

The demo lives in its own folder, never the real Career Hub folder, and holds only invented people,
companies, and postings. It makes no model calls, no network requests, and no Keychain reads: its
database key is a throwaway file inside the demo folder. Rows are made with the same functions the
rest of the app uses (`store_jobs`, `track`, `log_interaction`, `react`), so the demo stays in step
with the schema.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from careerhub.core.files import write_private
from careerhub.core.migrate import migrate
from careerhub.core.models import (
    Assessment,
    Company,
    Job,
    Person,
    Proposal,
    RunLog,
    Source,
)
from careerhub.core.paths import DATA_DIR_ENV, default_data_dir
from careerhub.core.settings import Settings, save_settings
from careerhub.core.storage import open_database, session_factory
from careerhub.modules.jobs.applications import set_follow_up, set_next_step, track
from careerhub.modules.jobs.reactions import react
from careerhub.modules.jobs.store import NormalizedJob, store_jobs
from careerhub.modules.network.interactions import log_interaction
from careerhub.profile.readiness import set_scoring

#: Marks a folder as a demo folder; `--reset` only ever removes a folder that has this file.
MARKER = ".careerhub-demo"
LABEL = "Demo data: invented people, companies, and jobs. Nothing here is real."

_SCHOOL = "Northgate University"

_PROFILE = f"""---
name: Alex Demo
education:
  - school: {_SCHOOL}
    degree: B.S. Computer Science
    graduation: 2027-05-15
skills: [Python, SQL, Git, Data analysis, Machine learning, Robotics]
interests: [robotics, applied machine learning, developer tools]
---
Invented profile for the demo. Nothing here belongs to a real person.
"""

_PREFERENCES = """hard_constraints:
  locations: [Boston, New York, San Francisco, Remote]
weekly_time_budget_hours: 6
work_rights: [US]
"""

# company | title | place | mode (h/o/r) | score | age in days | deadline in days | pay in $k |
# role focus | visa (yes/no, jobs abroad only)
_ROWS = """
Acme Robotics|Robotics Software Engineer, New Grad|Boston, MA|h|91|1|9|125|robot control|
Acme Robotics|Machine Learning Engineer, Perception|Boston, MA|h|86|2||135|computer vision|
Brightwater Health|Data Analyst, Early Career|New York, NY|o|82|3|14|95|health analytics|
Cobalt Cloud|Software Engineer I|Remote (US)|r|80|1||120|backend services|
Delta Freight|Software Engineer, Routing|Chicago, IL|h|78|5|20|110|optimization|
Evergreen Energy|Data Scientist, New Grad|San Francisco, CA|h|77|4||140|forecasting|
Fjord Labs|Developer Tools Engineer|Remote (US)|r|76|2||130|developer tools|
Granite Analytics|Junior Machine Learning Engineer|New York, NY|h|74|7|12|115|applied ML|
Harbor Games|Gameplay Engineer, New Grad|Austin, TX|o|73|6||100|game systems|
Acme Robotics|Embedded Software Engineer|Boston, MA|o|72|9||118|firmware|
Ironbridge Systems|Software Engineer, Infrastructure|Boston, MA|h|70|8|6|122|infrastructure|
Juniper Maps|Geospatial Data Engineer|Remote (US)|r|69|3||112|geospatial data|
Kestrel Aerospace|Flight Software Engineer|Austin, TX|o|68|10|30|128|flight software|
Lumen Learning|Backend Engineer, New Grad|Remote (US)|r|66|4||105|education tools|
Cobalt Cloud|Site Reliability Engineer I|Remote (US)|r|64|12||125|reliability|
Meridian Bank|Technology Analyst Program|New York, NY|o|62|6|4|90|financial systems|
Northwind Retail|Data Engineer, Early Career|Chicago, IL|h|60|14||98|data pipelines|
Orchard Bio|Computational Biology Engineer|Boston, MA|o|59|11|25|108|bioinformatics|
Pioneer Auto|Autonomy Software Engineer|San Francisco, CA|o|58|2||150|autonomy|
Quartz Security|Security Engineer, New Grad|Remote (US)|r|56|5||118|application security|
Redwood Media|Full-Stack Engineer I|San Francisco, CA|h|54|15||120|web apps|
Summit Telecom|Network Software Engineer|Chicago, IL|o|52|20||100|networking|
Tidewater Shipping|Operations Data Analyst|Boston, MA|o|50|25||85|logistics|
Umber Fashion|Data Analyst, Merchandising|New York, NY|h|47|9||80|retail analytics|
Vantage Labs Berlin|Software Engineer, Research Tools|Berlin, Germany|o|71|6|18||research tools|yes
Willow Systems London|Graduate Software Engineer|London, United Kingdom|h|67|8|||graduate program|no
Xenon Fintech|Quantitative Developer|New York, NY|o|61|3|7|160|trading systems|
Yarrow Foods|IT Support Analyst|Austin, TX|o|30|18||60|support|
Zephyr Staffing|Senior Staff Engineer|Remote (US)|r|20|5||210|platform|
Acme Robotics|Senior Controls Engineer|Boston, MA|o|18|7||170|controls|
"""
_MODES = {"h": "hybrid", "o": "onsite", "r": "remote"}
_JOBS = tuple(
    (
        c,
        t,
        pl,
        _MODES[m],
        int(sc),
        int(age),
        int(dl) if dl else None,
        int(pay) if pay else None,
        visa or "unknown",
        focus,
    )
    for c, t, pl, m, sc, age, dl, pay, focus, visa in (
        (line.split("|") + [""])[:10] for line in _ROWS.strip().splitlines()
    )
)

# Jobs the filters ruled out (by index into _JOBS) and the reason shown on the page.
_FILTERED = {
    28: "Needs 8+ years of experience; you have none yet",
    29: "Senior level; outside your entry-level window",
}

# (name, title, company, school, how known)
_PEOPLE = (
    ("Parker Example", "Engineering Manager", "Acme Robotics", _SCHOOL, "Alumni club mentor"),
    ("Jordan Sample", "Software Engineer", "Acme Robotics", None, "Former internship teammate"),
    ("Casey Placeholder", "Data Scientist", "Granite Analytics", _SCHOOL, "Alumni list"),
    ("Morgan Fiction", "Recruiter", "Cobalt Cloud", None, "Met at a career fair"),
    ("Riley Invented", "Staff Engineer", "Fjord Labs", _SCHOOL, "Alumni list"),
    ("Taylor Madeup", "Analyst", "Brightwater Health", None, "Classmate's sibling"),
    ("Sage Notreal", "Product Manager", "Evergreen Energy", None, "Hackathon judge"),
    ("Drew Fakename", "Research Engineer", "Kestrel Aerospace", _SCHOOL, "Alumni list"),
)


def real_folders() -> list[Path]:
    """Return the folders the demo must never use: the standard data folder and any override."""
    folders = [default_data_dir()]
    override = os.environ.get(DATA_DIR_ENV)
    if override and not (Path(override).expanduser() / MARKER).exists():  # not a demo folder
        folders.append(Path(override).expanduser())
    return folders


def demo_folder() -> Path:
    """Return the demo's folder, next to the real one (`CareerHubDemo`), never inside it."""
    real = default_data_dir()
    return real.with_name(real.name + "Demo")


def check_folder(folder: Path) -> None:
    """Refuse a folder that is, sits inside, or contains the real data folder."""
    target = folder.expanduser().resolve()
    for real in real_folders():
        base = real.resolve()
        if target == base or base in target.parents or target in base.parents:
            raise ValueError(f"{folder} is the real Career Hub folder; the demo never uses it.")


class FileKeyStore:
    """A throwaway key store: one file in the demo folder. Never the Keychain."""

    def __init__(self, folder: Path) -> None:
        """Keep secrets as files under `folder`."""
        self._folder = folder

    def get(self, name: str) -> str | None:
        """Return the secret called `name`, or None."""
        path = self._folder / f"{name}.key"
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def set(self, name: str, value: str) -> None:
        """Store `value` as the secret called `name`."""
        write_private(self._folder / f"{name}.key", value + "\n")

    def delete(self, name: str) -> None:
        """Remove the secret called `name`; a missing one is ignored."""
        (self._folder / f"{name}.key").unlink(missing_ok=True)


def reset_folder(folder: Path) -> None:
    """Remove an existing demo folder so it can be rebuilt. Only a folder marked as a demo goes."""
    check_folder(folder)
    if folder.exists():
        if not (folder / MARKER).exists():
            raise ValueError(f"{folder} was not made by `hub demo`; leaving it alone.")
        shutil.rmtree(folder)


def _calendar(now: datetime) -> str:
    """Return an iCalendar file with a few invented busy times this week."""

    def stamp(moment: datetime) -> str:
        return moment.strftime("%Y%m%dT%H%M%SZ")

    day = now.replace(hour=15, minute=0, second=0, microsecond=0)
    events = []
    for n, (offset, hours, title) in enumerate(
        ((0, 1, "Study group"), (1, 2, "Lab section"), (2, 1, "Club meeting"), (3, 3, "Shift"))
    ):
        start = day + timedelta(days=offset)
        events += [
            "BEGIN:VEVENT",
            f"UID:demo-{n}@careerhub.invalid",
            f"DTSTAMP:{stamp(now)}",
            f"DTSTART:{stamp(start)}",
            f"DTEND:{stamp(start + timedelta(hours=hours))}",
            f"SUMMARY:{title}",
            "END:VEVENT",
        ]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Career Hub demo//EN", *events]
    return "\r\n".join([*lines, "END:VCALENDAR", ""])


def _seed_jobs(session: Session, now: datetime) -> list[Job]:
    """Store the invented postings (via `store_jobs`) and their scores; return the jobs in order."""
    source = Source(
        kind="greenhouse", name="Demo board", url="https://demo.invalid/board", status="approved"
    )
    source.last_fetched_at = now - timedelta(hours=6)
    source.health = "ok"
    session.add(source)
    session.commit()
    listing = [
        NormalizedJob(
            external_id=str(n),
            company_name=company,
            title=title,
            url=f"https://demo.invalid/jobs/{n}",
            location=place,
            raw_text=f"{title} at {company}. Invented posting for the demo.",
            raw_payload={"demo": True},
        )
        for n, (company, title, place, *_rest) in enumerate(_JOBS, start=1)
    ]
    store_jobs(session, source, listing, now=now)
    jobs = sorted(session.query(Job).all(), key=lambda job: int(job.external_id))
    for index, (job, row) in enumerate(zip(jobs, _JOBS, strict=True)):
        company, title, place, mode, score, age, deadline, pay, visa, focus = row
        job.first_seen_at = job.last_seen_at = now - timedelta(days=age)
        fields: dict[str, Any] = {
            "level": "new_grad",
            "remote": mode,
            "visa_sponsorship": visa,
            "role_focus": focus,
            "summary": f"{company} is hiring for {focus}. Invented text for the demo.",
            "required_skills": ["Python", "Git"],
        }
        if deadline is not None:
            fields["deadline"] = (now + timedelta(days=deadline)).date().isoformat()
        if pay is not None:
            fields.update(salary_min_usd=pay * 1000, salary_max_usd=(pay + 20) * 1000)
        job.structured_fields = {"fields": fields}
        refused = _FILTERED.get(index)
        session.add(
            Assessment(
                job_id=job.id,
                filter_passed=refused is None,
                filter_results={"reasons": [refused]} if refused else {},
                fit_score=None if refused else float(score),
                criteria_scores=[
                    {
                        "name": "Field",
                        "score": min(10, score // 10 + 1),
                        "reason": f"Matches {focus}",
                    },
                    {"name": "Level", "score": 9, "reason": "Written for new graduates"},
                    {
                        "name": "Place",
                        "score": 8 if mode != "onsite" else 6,
                        "reason": f"{place}, {mode}",
                    },
                ],
                explanation=(
                    f"Strong match for your {focus} interest. The role is aimed at new graduates "
                    f"and sits in {place}."
                ),
                model_used="demo",
                prompt_version=1,
                rubric_version=1,
            )
        )
    session.commit()
    return jobs


def _seed_network_and_tracker(session: Session, jobs: list[Job], now: datetime) -> None:
    """Add people with warm paths, applications in several statuses, interactions, and a log."""
    people: dict[str, Person] = {}
    for name, title, company, school, how in _PEOPLE:
        firm = session.query(Company).filter_by(name=company).one()
        person = Person(
            company_id=firm.id,
            full_name=name,
            title=title,
            origin="alumni_list" if school else "linkedin_export",
            how_known=how,
            schools=[school] if school else None,
        )
        session.add(person)
        people[name] = person
    session.commit()
    day = timedelta(days=1)
    for job, status in zip(
        jobs[3:9],
        ("interested", "reached_out", "applied", "interviewing", "offer", "rejected"),
        strict=True,
    ):
        track(session, job.id, status, now=now - 5 * day)
    applied = track(session, jobs[2].id, "applied", now=now - 12 * day)
    set_follow_up(session, applied.id, now - day)  # overdue
    set_next_step(session, applied.id, "Send a short thank-you note")
    interviewing = track(session, jobs[6].id, "interviewing", now=now - 3 * day)
    set_follow_up(session, interviewing.id, now + 2 * day)
    log_interaction(
        session, people["Parker Example"].id, "email", "Asked about the team", now - 6 * day, now
    )
    log_interaction(
        session,
        people["Casey Placeholder"].id,
        "linkedin",
        "Coffee chat set up",
        now - 2 * day,
        now + 3 * day,
    )
    log_interaction(
        session, people["Riley Invented"].id, "call", "Talked about developer tools", now - 20 * day
    )
    react(session, jobs[11].id, "not_interested", "location")
    run = RunLog(
        step="nightly",
        status="succeeded",
        started_at=now - timedelta(hours=7),
        finished_at=now - timedelta(hours=6, minutes=40),
        items_processed=30,
    )
    failed = RunLog(
        step="fetch",
        status="failed",
        started_at=now - timedelta(days=2),
        finished_at=now - timedelta(days=2) + timedelta(minutes=2),
        error_summary="Demo board did not answer (invented failure).",
    )
    session.add_all([run, failed])
    session.add(
        Proposal(
            kind="preferences",
            title="Also look in Seattle, WA",
            body="You asked about Seattle in the conversation (invented).",
            origin="conversation",
            target="preferences",
            diff="locations:\n+  - Seattle",
        )
    )
    session.commit()


def build_demo(folder: Path, now: datetime | None = None) -> Path:
    """Create the demo folder with invented data and point Career Hub at it; return the folder.

    Sets `CAREERHUB_DATA_DIR` for this process so settings, profile files, and the database all
    resolve inside the demo folder. Refuses the real folder.
    """
    check_folder(folder)
    if folder.exists() and any(folder.iterdir()) and not (folder / MARKER).exists():
        raise ValueError(
            f"{folder} already exists and was not made by `hub demo`; leaving it alone."
        )
    now = now or datetime.now(UTC)
    fresh = not folder.exists() or not any(folder.iterdir())
    before = os.environ.get(DATA_DIR_ENV)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ[DATA_DIR_ENV] = str(folder)
    try:
        (folder / "profile").mkdir(exist_ok=True)
        write_private(folder / "profile" / "profile.md", _PROFILE)
        write_private(folder / "profile" / "preferences.yaml", _PREFERENCES)
        calendar = folder / "demo-calendar.ics"
        write_private(calendar, _calendar(now))
        save_settings(Settings(calendar_files=(str(calendar),)))
        set_scoring(True, now.date())
        engine = open_database(FileKeyStore(folder))
        migrate(engine)
        with session_factory(engine)() as session:
            jobs = _seed_jobs(session, now)
            _seed_network_and_tracker(session, jobs, now)
        engine.dispose()
        write_private(folder / MARKER, "Made by `hub demo`. Safe to delete.\n")  # last: built
    except BaseException:
        # A half-built demo must not look finished: remove what this call made.
        if fresh:
            shutil.rmtree(folder, ignore_errors=True)
        if before is None:
            os.environ.pop(DATA_DIR_ENV, None)
        else:
            os.environ[DATA_DIR_ENV] = before
        raise
    return folder
