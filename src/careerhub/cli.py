"""The `hub` command: `hub profile show` and `hub sources ...`."""

from __future__ import annotations

import getpass
import logging
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from careerhub.core.backup import RestoreError, list_backups, restore_backup
from careerhub.core.companies import (
    decide_merge,
    find_companies,
    list_merges,
    pending_merges,
    split_company,
    suggest_merges,
)
from careerhub.core.migrate import migrate
from careerhub.core.models import Company, utcnow
from careerhub.core.paths import DATA_DIR_ENV, database_path
from careerhub.core.secrets import (
    DATABASE_KEY_NAME,
    KeychainKeyStore,
    KeyStore,
    SecretsError,
)
from careerhub.core.settings import Settings, SettingsError, load_settings
from careerhub.core.storage import StorageError, WrongKeyError, open_database, session_factory
from careerhub.llm.cache import ResponseCache
from careerhub.llm.commands import (
    models_add,
    models_add_custom,
    models_list,
    models_remove,
    models_routes,
)
from careerhub.modules.attention import attention_state
from careerhub.modules.jobs import (
    FetchError,
    SourceNotApproved,
    UnknownSource,
    approve_source,
    disable_source,
    list_sources,
    propose_source,
    reject_source,
)
from careerhub.modules.jobs.adapters import ADAPTERS, BOARD_KINDS
from careerhub.modules.jobs.careers_page import track_company
from careerhub.modules.jobs.find_boards import find_boards
from careerhub.modules.jobs.manual import (
    ManualJobError,
    add_job,
    classify,
    guess_from_link,
    set_manual_job_closed,
)
from careerhub.modules.jobs.scoring import profile_snapshot
from careerhub.modules.jobs.targets import (
    TargetsError,
    load_targets,
    matches_interests,
    merge_import,
    read_targets,
    save_targets,
    set_enabled,
    set_unsupported,
)
from careerhub.modules.network import (
    UnknownPerson,
    find_people,
    import_alumni,
    preview_alumni,
    tag_person,
    warm_paths,
)
from careerhub.modules.network.mappings import (
    SavedMapping,
    forget_mapping,
    list_mappings,
    load_mapping,
    save_mapping,
)
from careerhub.modules.network.schools import my_schools
from careerhub.modules.network.schools_table import ambiguous_choices
from careerhub.modules.notify import MacNotifier, notify_once_a_day
from careerhub.modules.plan import suggest_week
from careerhub.modules.plan.ics import IcsError
from careerhub.pipeline import run_nightly, run_refresh
from careerhub.power import read_power_source
from careerhub.profile.changes import check, force_rescore
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import UnknownExtraction, accepted_facts, decide, facts_text, pending
from careerhub.profile.files import ProfileFileError, load_preferences, load_profile_doc
from careerhub.profile.models import PlanSettings, Preferences, ProfileDoc
from careerhub.profile.profile import Profile, format_profile, load_profile
from careerhub.profile.readiness import current_readiness, missing_items, set_scoring
from careerhub.schedule import LABEL, REFRESH_LABEL, install_plist, launchctl_commands
from careerhub.web.shortlist import write_shortlist

USAGE = """Usage:
  hub profile show
  hub ingest greenhouse|lever|ashby --company <board>   (the source must be approved)
  hub plan                                   (suggested blocks for the next 7 days)
  hub paths <company>                        (who you know there, strongest first)
  hub people find <text>                     (by name or company)
  hub people tag <id> [--school 'Tufts University'] <how you know them>
  hub people import-alumni <file> [--group G] [--sheet S] [--map field=Header] [--preview]
                                             (CSV, TSV or Excel; also `hub alumni import`; the
                                             --map/--sheet/--group are remembered per file name)
  hub alumni mappings | forget <file>        (the remembered column mappings)
  hub schedule install                       (write the nightly launchd job; prints how to start it)
  hub refresh                                (light midday run: new jobs and filters only, no paid
                                             models; turn the schedule on with midday_refresh_on)
  hub backup list | restore <file> --to <new folder>
                          (restores into a NEW folder and checks it; never touches your live data)
  hub nightly [--if-plugged-in]              (run the whole pipeline once; skip on battery)
  hub attention [--json | --notify]          (menu-bar count; --notify: morning banner, once a day)
  hub web [--port N]                         (the local web app on 127.0.0.1; prints a link to open)
  hub demo [--reset] [--port N]              (try the web app with made-up data in its own folder;
                                             never your real data)
  hub add-job <url> [--company C] [--title T] [--text-file F]   (any link; LinkedIn and Handshake
                                             are never requested; text can also come from stdin)
  hub jobs close <id> | reopen <id>      (only for jobs you added by link; nothing is deleted)
  hub companies suggestions | merge <id> | skip <id>   (near-duplicate company names)
  hub companies merges | split <company> <name>   (list merges; undo one, with a yes/no question)
  hub targets find-boards | note <name> <ats> | import <file> | list | enable-matching
  hub targets add <name> [--careers-url URL]   (follow a company; looks for its board; fetches
                          that one page, honoring robots.txt, never LinkedIn or Handshake)
  hub targets enable <name>... | disable <name>...
                          (companies you follow; the Engine list is in docs/seeds)
  hub models list | routes
  hub models add <anthropic|openai|gemini --tier free|paid|local|preset>   (asks for the key)
  hub models remove <same names>
  hub models add-custom <name> <kind> <model> [--base-url U] [--input N] [--output N]
                        [--no-training]   (without it the model is assumed to train on your data)
  hub cache clear [--expired]               (delete saved model answers; prints how many)
  hub profile ready [--off]                 (check the profile, then switch scoring on or off)
  hub profile facts                         (what was read from your resume and transcript)
  hub profile facts accept <number|all> | reject <number>
  hub score --all                           (score every job again on the next nightly run)
  hub shortlist build                        (write the shortlist page)
  hub sources list
  hub sources add <kind> <name> <url>     (saved as proposed; nothing is fetched yet)
    for themuse also: --category <c> --level <l> --location <l> --pages <n>
  hub sources approve|reject|disable <id>"""


def main(argv: list[str] | None = None, store: KeyStore | None = None) -> int:
    """Run the command. `store` is the database key store (tests pass an in-memory one)."""
    args = sys.argv[1:] if argv is None else argv
    store = store or KeychainKeyStore()
    try:
        if args[:2] == ["profile", "ready"] and args[2:] in ([], ["--off"]):
            return _with_session(store, lambda s: _profile_ready(s, off=bool(args[2:])))
        if args[:2] == ["profile", "facts"]:
            return _with_session(store, lambda s: _profile_facts(s, args[2:]))
        if args == ["profile", "show"]:
            print(format_profile(_load_profile(store)))
            print(_readiness_summary())
            print(_rescore_summary(store))
            note = _ambiguous_schools_note()
            if note:
                print(note)
            return 0
        if args == ["score", "--all"]:
            return _score_all()
        if (
            len(args) == 4
            and args[0] == "ingest"
            and args[1] in ADAPTERS
            and args[2] == "--company"
        ):
            return _with_session(store, lambda s: _ingest(s, args[3], kind=args[1]))
        if args == ["plan"]:
            return _with_session(store, _plan)
        if args[:1] == ["paths"] and len(args) >= 2:
            return _with_session(store, lambda s: _paths(s, " ".join(args[1:])))
        if args[:2] == ["people", "find"] and len(args) >= 3:
            return _with_session(store, lambda s: _people_find(s, " ".join(args[2:])))
        if args[:2] == ["people", "tag"] and len(args) >= 4 and args[2].isdigit():
            rest = args[3:]
            school = None
            if rest[0] == "--school" and len(rest) >= 2:
                school, rest = rest[1], rest[2:]
            return _with_session(
                store, lambda s: _people_tag(s, int(args[2]), " ".join(rest), school)
            )
        if args[:2] in (["people", "import-alumni"], ["alumni", "import"]) and len(args) >= 3:
            return _with_session(store, lambda s: _people_import(s, Path(args[2]), args[3:]))
        if args == ["alumni", "mappings"]:
            return _alumni_mappings()
        if args[:2] == ["alumni", "forget"] and len(args) == 3:
            return _alumni_forget(args[2])
        if args == ["schedule", "install"]:
            return _schedule_install()
        if args == ["refresh"]:
            return _nightly(store, light=True)
        if args == ["backup", "list"]:
            return _backup_list()
        if len(args) == 5 and args[:2] == ["backup", "restore"] and args[3] == "--to":
            return _backup_restore(store, Path(args[2]), Path(args[4]))
        if args[:1] == ["nightly"] and args[1:] in ([], ["--if-plugged-in"]):
            return _nightly(store, if_plugged_in=bool(args[1:]))
        if args[:1] == ["attention"] and args[1:] in ([], ["--json"], ["--notify"]):
            return _with_session(store, lambda s: _attention(s, args[1:]))
        if args[:1] == ["web"]:
            return _web(args[1:])
        if args[:1] == ["demo"]:
            return _demo(args[1:])
        if args[:1] == ["add-job"] and len(args) >= 2 and len(args) % 2 == 0:
            return _with_session(store, lambda s: _add_job(s, args[1], args[2:]))
        if (
            len(args) == 3
            and args[0] == "jobs"
            and args[1] in ("close", "reopen")
            and args[2].isdigit()
        ):
            return _with_session(store, lambda s: _jobs_close(s, args[1] == "close", int(args[2])))
        if args[:1] == ["companies"]:
            return _with_session(store, lambda s: _companies(s, args[1:]))
        if args == ["targets", "find-boards"]:
            return _with_session(store, _find_boards)
        if args[:2] == ["targets", "add"] and len(args) >= 3:
            return _with_session(store, lambda s: _targets_add(s, args[2:]))
        if args[:1] == ["targets"] and len(args) >= 2:
            return _targets(args[1:])
        if args[:1] == ["models"] and len(args) >= 2:
            return _models(store, args[1:])
        if args[:2] == ["cache", "clear"] and args[2:] in ([], ["--expired"]):
            return _cache_clear(store, expired_only=bool(args[2:]))
        if args == ["shortlist", "build"]:
            return _with_session(store, _shortlist_build)
        if args[:2] == ["sources", "list"] and len(args) == 2:
            return _with_session(store, _sources_list)
        if args[:2] == ["sources", "add"] and len(args) >= 5 and len(args) % 2 == 1:
            return _with_session(
                store, lambda s: _sources_add(s, args[2], args[3], args[4], args[5:])
            )
        if len(args) == 3 and args[0] == "sources" and args[1] in _DECISIONS and args[2].isdigit():
            return _with_session(store, lambda s: _sources_decide(s, args[1], int(args[2])))
    except (ProfileFileError, SettingsError) as error:
        print(f"Problem with a settings or profile file: {error}")
        return 1
    except (StorageError, SecretsError) as error:
        print(f"Can't open the database ({type(error).__name__}).")
        return 1
    print(USAGE)
    return 2


def _load_profile(store: KeyStore) -> Profile:
    """Load the profile, with the connection count if the database can be opened."""
    try:
        engine = open_database(store)
    except (StorageError, SecretsError):
        return load_profile()  # files only; the count shows as unknown
    migrate(engine)
    with session_factory(engine)() as session:
        return load_profile(session)


def _with_session(store: KeyStore, action: Callable[[Session], int]) -> int:
    """Open the database (migrating it) and run `action` with a session."""
    engine = open_database(store)
    migrate(engine)
    with session_factory(engine)() as session:
        return action(session)


PLAN_NUMBERS = (
    "apply_minutes",
    "outreach_minutes",
    "daily_share",
    "min_gap_minutes",
    "tasks_per_plan",
)


def _plan_sources(preferences: Preferences | None) -> str:
    """Say which plan values come from preferences.yaml and which are defaults."""
    chosen = preferences.plan if preferences else PlanSettings()
    mine = set(chosen.model_fields_set)
    parts = []
    for name in (
        "apply_minutes",
        "outreach_minutes",
        "daily_share",
        "min_gap_minutes",
        "tasks_per_plan",
    ):
        origin = "yours" if name in mine else "default"
        parts.append(f"{name.replace('_', ' ')} {getattr(chosen, name)} ({origin})")
    hours = chosen.working_hours
    parts.append(
        f"hours {hours.start}-{hours.end}"
        f"{' with weekday changes' if hours.weekdays else ''} "
        f"({'yours' if 'working_hours' in mine else 'default'})"
    )
    return "  Plan settings: " + "; ".join(parts) + "."


def _plan(session: Session) -> int:
    """Print the suggested week. It only suggests: nothing is written to a calendar."""
    settings = load_settings()
    preferences = load_preferences()
    budget = preferences.weekly_time_budget_hours if preferences else 5.0
    try:
        plan = suggest_week(
            session, settings, budget, utcnow(), preferences.plan if preferences else None
        )
    except IcsError as error:
        print(f"Can't read a calendar file: {error}")
        return 1
    zone = ZoneInfo(settings.timezone)
    hours, minutes = divmod(plan.planned_minutes, 60)
    print(f"Suggested plan ({hours}h{minutes:02d}m of {budget:g}h):")
    print(_plan_sources(preferences))
    for problem in plan.problems:
        print(f"  Problem: {problem}")
    for block in plan.blocks:
        when = block.start.astimezone(zone)
        print(f"  {when:%a %b %d %H:%M}-{block.end.astimezone(zone):%H:%M}  {block.task.title}")
    for item in plan.unplaced:
        print(f"  not placed: {item.task.title} ({item.reason})")
    if not plan.blocks and not plan.unplaced:
        print("  Nothing to plan yet: no ranked jobs. Run `hub nightly` first.")
    return 0


def _paths(session: Session, company_text: str) -> int:
    matches = find_companies(session, company_text, _aliases())
    if len(matches) > 1:
        print(f"{company_text!r} matches several companies; be more specific:")
        print("\n".join(f"  {c.name}" for c in matches))
        return 1
    if not matches:
        print(f"No company matching {company_text!r}.")
        return 1
    company = matches[0]
    paths = warm_paths(session, company.id, limit=10)
    print(f"{company.name}:")
    for path in paths:
        print(
            f"  {path.name} ({path.title or 'no title'}): {'; '.join(path.reasons) or 'no details'}"
        )
    if not paths:
        print("  You don't know anyone here yet.")
    return 0


def _people_find(session: Session, text: str) -> int:
    people = find_people(session, text)
    for person in people:
        company = person.company.name if person.company else "-"
        columns = (
            person.id,
            person.full_name,
            company,
            person.title or "-",
            person.how_known or "-",
        )
        print("\t".join(str(column) for column in columns))
    if not people:
        print("No one found.")
    return 0


def _people_tag(session: Session, person_id: int, how_known: str, school: str | None = None) -> int:
    try:
        person = tag_person(session, person_id, how_known, school)
    except UnknownPerson:
        print(f"No person with id {person_id}.")
        return 1
    shown = f" (school: {', '.join(person.schools)})" if school and person.schools else ""
    print(f"{person.full_name}: {person.how_known or '(cleared)'}{shown}")
    return 0


def _people_import(session: Session, path: Path, options: list[str] | None = None) -> int:
    """Import (or with --preview, only describe) an alumni list; see `modules/network/alumni.py`."""
    group, sheet, overrides, preview = None, None, {}, False
    words = list(options or [])
    while words:
        flag = words.pop(0)
        if flag == "--preview":
            preview = True
        elif flag in ("--group", "--sheet", "--map") and words:
            value = words.pop(0)
            if flag == "--group":
                group = value
            elif flag == "--sheet":
                sheet = value
            elif "=" in value:
                key, _, header = value.partition("=")
                overrides[key.strip()] = header
            else:
                print("--map takes field=Header, for example --map company='Current Employer'.")
                return 2
        else:
            print(USAGE)
            return 2
    given = bool(overrides) or sheet is not None or group is not None
    saved = load_mapping(path.name)
    if saved is not None:  # what was saved applies; flags given now win and update it
        overrides = {**saved.mapping, **overrides}
        sheet, group = sheet or saved.sheet, group or saved.group
    try:
        if preview:
            shown = preview_alumni(session, path, group, sheet, overrides, _aliases())
            print(f"Header found on row {shown.header_row}. Columns:")
            for name, header in shown.mapping.items():
                print(f"  {header!r} -> {name}")
            for header in shown.ignored:
                print(f"  {header!r} -> ignored")
            report = shown.report
            print(
                f"Would add {report.created}, tag {report.tagged}, leave {report.unchanged}, "
                f"skip {report.skipped}."
            )
            return 0
        report = import_alumni(session, path, group, sheet, overrides, aliases=_aliases())
        suggest_merges(session)
        if given:
            save_mapping(path.name, SavedMapping(overrides, sheet, group))
    except ProfileFileError as error:
        print(error)
        if saved is not None and "no column called" in str(error):
            print(
                f"The mapping saved for {path.name} no longer fits this file. Pass --map again, "
                f"or run `hub alumni forget {path.name}` to start over."
            )
        return 1
    missing = f" (missing {' or '.join(report.skipped_for)})" if report.skipped_for else ""
    if report.truncated:
        print("The file has more rows than Career Hub reads (50,000); the rest were ignored.")
    print(
        f"{report.created} added, {report.tagged} tagged as alumni, {report.unchanged} unchanged, "
        f"{report.skipped} rows skipped{missing}."
    )
    return 0


def _schedule_install() -> int:
    """Write the launchd job(s); print how to start and stop each. Nothing is started here."""
    settings = load_settings()
    jobs = [("nightly", settings.nightly_run_time, LABEL)]
    if settings.midday_refresh_on:
        jobs.append(("refresh", settings.midday_refresh_time, REFRESH_LABEL))
    for command, run_time, label in jobs:
        require_power = command == "nightly" and settings.nightly_require_power
        path = install_plist(run_time, command=command, require_power=require_power)
        start, stop = launchctl_commands(path, label)
        print(
            f"Wrote {path}. It is not running yet. To start it:\n  {start}\n"
            f"To stop it later:\n  {stop}"
        )
    return 0


def _backup_list() -> int:
    """Print the backups, newest first, with their sizes."""
    found = list_backups()
    if not found:
        print("No backups yet. The nightly run writes one.")
    for path in reversed(found):
        print(f"{path}  ({path.stat().st_size // 1024} KB)")
    return 0


def _backup_restore(store: KeyStore, backup: Path, folder: Path) -> int:
    """Restore `backup` into the new `folder`, check it, and say how to switch over."""
    key = store.get(DATABASE_KEY_NAME)  # read-only: a restore must not create a new key
    if key is None:
        print("Restore failed: there is no database key in the Keychain to open the backup with.")
        return 1
    try:
        report = restore_backup(backup, folder, key)
    except (RestoreError, WrongKeyError, ValueError) as error:
        print(f"Restore failed: {error}")
        return 1
    total = sum(report.counts.values())
    print(f"Restored {backup.name} to {report.path}: {len(report.counts)} tables, {total} rows,")
    print("integrity check passed and row counts match the backup.")
    print("Nothing was changed in your live data. To switch over, quit Career Hub, move")
    print(f"{database_path()} aside, then copy {report.path} to that place.")
    return 0


def _nightly(
    store: KeyStore,
    light: bool = False,
    if_plugged_in: bool = False,
    power: Callable[[], str] = read_power_source,
) -> int:
    """Run the pipeline (the light midday version if `light`) once; exit 1 if it failed outright.

    With `if_plugged_in`, a Mac on battery skips the run (a skip is not a failure: exit 0, and no
    run is recorded, so the page keeps showing the last real run). An unreadable power source
    runs the job anyway.
    """
    if if_plugged_in and power() == "battery":
        print("Skipped: the Mac is on battery power. The next scheduled run will try again.")
        logging.getLogger("careerhub.nightly").info("skipped: on battery")
        return 0
    engine = open_database(store)
    migrate(engine)
    report = (run_refresh if light else run_nightly)(engine, store)
    print(report.summary())
    return 1 if any(p.startswith("run failed") for p in report.problems) else 0


def _attention(session: Session, options: list[str]) -> int:
    """`hub attention [--json | --notify]`: print what the menu-bar icon shows (counts only).

    With `--notify`, show the morning notification (at most once a day) on this Mac.
    """
    import json

    state = attention_state(session)
    if options == ["--notify"]:
        try:
            today = utcnow().astimezone(ZoneInfo(load_settings().timezone)).date()  # the user's day
            shown = notify_once_a_day(state, today, MacNotifier())
        except (RuntimeError, OSError) as error:
            print(f"Could not show the notification: {error}")
            return 1
        print(f"Showed: {shown.body}" if shown else "Nothing to show (or already shown today).")
        return 0
    print(json.dumps(state.as_json(), sort_keys=True) if options else state.description)
    return 0


def _port_from(options: list[str], default: int) -> int | None:
    """Read `--port N` from `options`; None (after printing the usage) for anything else."""
    port = default
    words = list(options)
    while words:
        flag = words.pop(0)
        if flag == "--port" and words and words[0].isdigit() and 1024 <= int(words[0]) <= 65535:
            port = int(words.pop(0))
        elif flag == "--host":
            print("hub web only listens on 127.0.0.1; it cannot be told to use another address.")
            return None
        else:
            print(USAGE)
            return None
    return port


def _serve(port: int, app_for: Callable[[str], Any]) -> int:
    """Print the one-time link and serve `app_for(token)` on 127.0.0.1 until Ctrl-C."""
    import secrets

    import uvicorn

    token = secrets.token_urlsafe(32)
    print(f"Career Hub is at http://127.0.0.1:{port}/?t={token}")
    print("Open that link; press Ctrl-C to stop.")
    uvicorn.run(app_for(token), host="127.0.0.1", port=port, log_level="warning")
    return 0


def _web(options: list[str]) -> int:
    """`hub web [--port N]`: serve the web app on 127.0.0.1 only and print the link to open it."""
    port = _port_from(options, load_settings().web_port)
    if port is None:
        return 2
    from careerhub.web.app import create_app, make_session_opener

    return _serve(port, lambda token: create_app(token, port, open_session=make_session_opener()))


def _demo(options: list[str]) -> int:
    """`hub demo [--reset] [--port N]`: the web app on invented data in its own folder."""
    from careerhub.demo import (
        LABEL,
        MARKER,
        FileKeyStore,
        build_demo,
        check_folder,
        demo_folder,
        reset_folder,
    )
    from careerhub.web.app import create_app, database_opens, make_session_opener

    reset = "--reset" in options
    # Not load_settings(): that would read (and create) the real data folder.
    port = _port_from([o for o in options if o != "--reset"], Settings().web_port)
    if port is None:
        return 2
    folder = demo_folder()
    try:
        check_folder(folder)
        if reset:
            reset_folder(folder)
        if not (folder / MARKER).exists():
            build_demo(folder)
            print(f"Made the demo folder {folder} with invented data.")
        else:
            os.environ[DATA_DIR_ENV] = str(folder)
    except ValueError as error:
        print(f"Couldn't start the demo. {error}")
        return 1
    keys = FileKeyStore(folder)
    print("Demo data only: no model calls, no network, no Keychain. Your real data is untouched.")
    return _serve(
        port,
        lambda token: create_app(
            token,
            port,
            lambda: database_opens(keys),
            make_session_opener(keys),
            keys,
            lambda: False,
            banner=LABEL,
        ),
    )


def _add_job(
    session: Session,
    url: str,
    options: list[str],
    client: httpx.Client | None = None,
    stdin: TextIO | None = None,
    ask: Callable[[str], str] = input,
) -> int:
    """Add a job from its link (see `modules/jobs/manual.py`); asks only for what is missing."""
    stdin = stdin or sys.stdin
    flags = dict(zip(options[::2], options[1::2], strict=True))
    if not set(flags) <= {"--company", "--title", "--text-file"}:
        print(USAGE)
        return 2
    company, title = flags.get("--company"), flags.get("--title")
    if classify(url) is None:
        guess = guess_from_link(url)
        if guess and not title:
            title = title or guess[0]
        if guess and not company:
            company = guess[1]
        if stdin.isatty():
            company = company or ask("Company: ").strip() or None
            title = title or ask("Job title: ").strip() or None
    text: str | None = None
    try:
        try:
            result = add_job(session, url, company, title, None, client, aliases=_aliases())
        except ManualJobError:
            if "--text-file" in flags:
                text = Path(flags["--text-file"]).read_text(encoding="utf-8")
            elif not stdin.isatty():
                text = stdin.read()
            result = add_job(session, url, company, title, text, client, aliases=_aliases())
    except (ManualJobError, OSError) as error:
        print(f"Couldn't add the job. {error}")
        return 1
    print(f"Added #{result.job.id} {result.job.title}: {result.how}. Run `hub nightly` to rank it.")
    return 0


def _find_boards(session: Session, client: httpx.Client | None = None) -> int:
    """Look for the job boards of followed companies; hits become proposed sources."""
    try:
        targets = load_targets()
        report = find_boards(session, targets, client)
        save_targets(targets)
    except TargetsError as error:
        print(error)
        return 1
    for name, hit in report.found:
        print(
            f"Found {name}: {hit.kind} board {hit.token!r} (proposed; approve it with hub sources)."
        )
    for name, hit in report.uncertain:
        print(
            f"Unsure about {name}: a {hit.kind} board {hit.token!r} answered, "
            "but it doesn't name them."
        )
    if report.missed:
        print("No board found for: " + ", ".join(report.missed))
    if report.stopped:
        print(report.stopped)
    return 0


def _targets_add(session: Session, args: list[str], client: httpx.Client | None = None) -> int:
    """`hub targets add <name> [--careers-url URL]`: follow a company and look for its board."""
    url: str | None = None
    words = list(args)
    if "--careers-url" in words:
        at = words.index("--careers-url")
        if at + 1 >= len(words):
            print(USAGE)
            return 2
        url = words[at + 1]
        del words[at : at + 2]
    if not words:
        print(USAGE)
        return 2
    try:
        targets = load_targets()
        lines = track_company(session, targets, " ".join(words), url, client, _aliases())
        save_targets(targets)
    except TargetsError as error:
        print(error)
        return 1
    print("\n".join(lines))
    return 0


def _targets(args: list[str]) -> int:
    """Run a `hub targets ...` command on `targets.yaml` (no network, no database)."""
    try:
        targets = load_targets()
        if args[0] == "import" and len(args) == 2:
            merged, added = merge_import(targets, read_targets(Path(args[1])))
            save_targets(merged)
            followed = sum(t.enabled for t in merged)
            print(f"Added {added} companies (disabled). You follow {followed}.")
            return 0
        if args[0] == "list" and len(args) == 1:
            for target in targets:
                board = f"{target.board_kind}:{target.board}" if target.board else "board unknown"
                if target.unsupported_ats and not target.board:
                    board = f"board not supported ({target.unsupported_ats})"
                mark = "x" if target.enabled else " "
                sectors = ", ".join(target.sectors) or "no sectors"
                print(f"[{mark}] {target.name} ({sectors}); {board}")
            print(f"{len(targets)} companies, {sum(t.enabled for t in targets)} followed.")
            return 0
        if args[0] in ("enable", "disable") and len(args) >= 2:
            unknown = set_enabled(targets, args[1:], args[0] == "enable")
            save_targets(targets)
            for name in unknown:
                print(f"No target named {name!r}.")
            return 1 if unknown else 0
        if args[0] == "note" and len(args) >= 3:
            missing = set_unsupported(targets, args[1], " ".join(args[2:]))
            save_targets(targets)
            print(f"No target named {args[1]!r}." if missing else "Noted.")
            return 1 if missing else 0
        if args[0] == "enable-matching" and len(args) == 1:
            doc = load_profile_doc()
            if doc is None or not doc.interests:
                print("Add interests to profile.md first.")
                return 1
            hits = [t for t in targets if matches_interests(t, doc.interests)]
            for target in hits:
                target.enabled = True
            save_targets(targets)
            print(f"Now following {len(hits)} companies whose sectors match your interests:")
            print("\n".join(f"  {t.name}" for t in hits))
            return 0
    except (TargetsError, ProfileFileError) as error:
        print(error)
        return 1
    print(USAGE)
    return 2


#: The `--flag value` options `hub models` accepts.
_MODEL_FLAGS = frozenset({"tier", "base_url", "input", "output"})


def _flags(args: list[str]) -> tuple[list[str], dict[str, str | bool]] | None:
    """Split `args` into plain words and `--flag value` pairs; None if a flag is malformed."""
    words: list[str] = []
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        if args[i] == "--no-training":
            flags["no_training"] = True
        elif args[i].startswith("--"):
            name = args[i][2:].replace("-", "_")
            # An unknown flag is an error: dropping a mistyped flag would silently save a
            # different value than the user meant.
            if name not in _MODEL_FLAGS or i + 1 >= len(args):
                return None
            flags[name] = args[i + 1]
            i += 1
        else:
            words.append(args[i])
        i += 1
    return words, flags


def _models(store: KeyStore, args: list[str]) -> int:
    """Run a `hub models ...` command."""
    parsed = _flags(args[1:])
    if parsed is None:
        print(USAGE)
        return 2
    words, flags = parsed
    tier = flags.get("tier")
    tier = tier if isinstance(tier, str) else None
    secret = lambda prompt: getpass.getpass(prompt)  # noqa: E731  (hidden input, never echoed)
    if args[0] == "list" and not words:
        return models_list(store, date.today())
    if args[0] == "routes" and not words:
        return models_routes(load_settings(), store)
    if args[0] in ("add", "remove") and len(words) == 1:
        if args[0] == "add":
            return models_add(store, words[0], tier, secret)
        return models_remove(store, words[0], tier, input)
    if args[0] == "add-custom" and len(words) == 3:
        options: dict[str, str | bool] = {
            "name": words[0],
            "kind": words[1],
            "model": words[2],
            **flags,
        }
        return models_add_custom(store, load_settings(), options, secret)
    print(USAGE)
    return 2


def _cache_clear(store: KeyStore, expired_only: bool) -> int:
    """Delete saved model answers (only the expired ones with `--expired`) and print the count."""
    engine = open_database(store)
    migrate(engine)
    cache = ResponseCache(session_factory(engine), load_settings().cache_ttl_days)
    removed = cache.prune() if expired_only else cache.clear()
    print(f"Removed {removed} saved model {'answer' if removed == 1 else 'answers'}.")
    return 0


def _rescore_summary(store: KeyStore) -> str:
    """Say whether the next nightly run will score jobs again because the profile changed."""
    doc, preferences = load_profile_doc(), load_preferences()
    if doc is None:
        return "\nRe-scoring: no profile yet."
    try:
        engine = open_database(store)
        migrate(engine)
        with session_factory(engine)() as session:
            facts = accepted_facts(session)
    except (StorageError, SecretsError):
        facts = None
    settings = load_settings()
    fingerprint = check(
        profile_snapshot(doc, facts, preferences),
        settings.score_typo_distance,
        settings.score_free_text_change_percent,
        save=False,
    )
    if fingerprint.first_run:
        return "\nRe-scoring: nothing has been scored yet, so the first run scores every job."
    if not fingerprint.reasons:
        return "\nRe-scoring: the profile has not changed enough; scores stay as they are."
    return "\nRe-scoring: the next nightly run will score jobs again because of:\n" + "\n".join(
        f"  - {reason}" for reason in fingerprint.reasons
    )


def _score_all() -> int:
    """`hub score --all`: make the next nightly run score and tailor every job again."""
    if force_rescore() is None:
        print("Nothing has been scored yet, so the first nightly run scores every job anyway.")
        return 0
    print("The next nightly run will score and tailor every job again (this can cost money).")
    return 0


def _ambiguous_schools_note() -> str:
    """Name any of your schools that is a short name for several (PSU), and how to settle it."""
    lines = []
    for school in my_schools(None):
        choices = ambiguous_choices(school)
        if choices:
            lines.append(
                f"  - {school!r} could mean: {'; '.join(choices)}. Under school_aliases in "
                "preferences.yaml, give the one you mean, for example:\n"
                f'      "{choices[0]}": ["{school}"]'
            )
    if not lines:
        return ""
    return "\nSchool names to settle (they match nothing until you do):\n" + "\n".join(lines)


def _readiness_summary() -> str:
    """Describe whether scoring is on and, if not, what the profile still needs."""
    readiness = current_readiness(None, load_settings().profile_min_skills)
    if readiness.on:
        return f"\nScoring: on since {readiness.since}."
    items = "\n".join(f"  - {item}" for item in readiness.missing) or "  (nothing missing)"
    return f"\nScoring: off. To switch it on, run `hub profile ready`. Still needed:\n{items}"


def _profile_ready(session: Session, off: bool) -> int:
    """Switch scoring on once the profile checklist passes, or off with `--off`."""
    if off:
        set_scoring(False, utcnow().date())
        print("Scoring is off. Nightly runs still ingest, read, and filter jobs.")
        return 0
    missing = missing_items(session, load_settings().profile_min_skills)
    if missing:
        print("The profile isn't ready yet. Still needed:")
        print("\n".join(f"  - {item}" for item in missing))
        return 1
    set_scoring(True, utcnow().date())
    print("Scoring is on. The next nightly run will score and write tailoring notes.")
    return 0


def _profile_facts(session: Session, args: list[str]) -> int:
    """List pending facts, or accept or reject one (nothing counts until accepted)."""
    if not args:
        rows = pending(session)
        for row in rows:
            print(f"#{row.id} from your {row.kind} (read by {row.model_used}):")
            facts = ProfileFacts.model_validate(row.facts)
            shown = facts_text(facts, ProfileDoc(name="")) or "(nothing found)"
            print("  " + shown.replace("\n", "\n  "))
        if not rows:
            print("No facts are waiting for review.")
        else:
            print("Accept with `hub profile facts accept <number|all>` or `reject <number>`.")
        return 0
    if args[0] == "accept" and len(args) == 2 and (args[1] == "all" or args[1].isdigit()):
        ids = [r.id for r in pending(session)] if args[1] == "all" else [int(args[1])]
        accept = True
    elif args[0] == "reject" and len(args) == 2 and args[1].isdigit():
        ids, accept = [int(args[1])], False
    else:
        print(USAGE)
        return 2
    try:
        for extraction_id in ids:
            decide(session, extraction_id, accept)
    except UnknownExtraction as error:
        print(error)
        return 1
    print(f"{'Accepted' if accept else 'Rejected'} {len(ids)} set(s) of facts.")
    return 0


def _alumni_mappings() -> int:
    """`hub alumni mappings`: list the file names with a saved column mapping (no people)."""
    saved = list_mappings()
    for name, fields in saved.items():
        print(f"  {name}: {', '.join(fields) or 'no overrides'}")
    if not saved:
        print("No saved column mappings.")
    return 0


def _alumni_forget(file_name: str) -> int:
    """`hub alumni forget <file>`: remove the saved mapping for a file name."""
    if forget_mapping(Path(file_name).name):
        print(f"Forgot the saved mapping for {Path(file_name).name}.")
        return 0
    print(f"No saved mapping for {Path(file_name).name}.")
    return 1


def _jobs_close(
    session: Session, close: bool, job_id: int, ask: Callable[[str], str] | None = None
) -> int:
    """`hub jobs close <id>` or `reopen <id>`: asks yes or no, then marks the job; never deletes."""
    verb = "close" if close else "reopen"
    if (ask or input)(f"{verb.capitalize()} job {job_id}? [y/N] ").strip().lower() not in (
        "y",
        "yes",
    ):
        print("Nothing changed.")
        return 1
    try:
        job = set_manual_job_closed(session, job_id, close)
    except ManualJobError as error:
        print(error)
        return 1
    print(f"Job {job_id} ({job.title}) is now {job.status}.")
    return 0


def _aliases() -> dict[str, str]:
    """Return the user's company aliases from preferences.yaml (none if there is no file)."""
    preferences = load_preferences()
    return preferences.alias_keys() if preferences else {}


def _companies(session: Session, args: list[str]) -> int:
    """`hub companies suggestions | merge <id> | skip <id>`: answer near-duplicate suggestions."""
    if args == ["suggestions"]:
        pending = pending_merges(session)
        for item in pending:
            print(f"  {item.id}: {item.title}")
        if not pending:
            print("No merge suggestions.")
        return 0
    if len(args) == 2 and args[0] in ("merge", "skip") and args[1].isdigit():
        try:
            print(decide_merge(session, int(args[1]), args[0] == "merge", _aliases()))
        except LookupError as error:
            print(error)
            return 1
        return 0
    if args == ["merges"]:
        for company, entry in list_merges(session):
            print(
                f"  {entry['name']} was merged into {company.name} "
                f"({len(entry['job_ids'])} job(s), {len(entry['person_ids'])} person/people)"
            )
        for company in session.query(Company).filter(Company.merged_keys.is_not(None)):
            logged = {e["name_key"] for e in company.merge_log or []}
            for key in company.merged_keys or []:
                if key not in logged:
                    print(f"  {key} was merged into {company.name} (before merges were logged)")
        return 0
    if len(args) == 3 and args[0] == "split":
        return _split(session, args[1], args[2])
    print("Usage: hub companies suggestions|merge <id>|skip <id>|merges|split <company> <name>")
    return 2


def _split(session: Session, company_text: str, name: str) -> int:
    """`hub companies split <company> <name>`: undo one merge, after a yes/no question."""
    matches = find_companies(session, company_text, _aliases())
    if len(matches) != 1:
        print(f"{company_text!r} matches {len(matches)} companies; be more specific.")
        return 1
    answer = input(f"Separate {name!r} from {matches[0].name} again? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("Nothing changed.")
        return 0
    try:
        print(split_company(session, matches[0], name, _aliases()))
    except LookupError as error:
        print(error)
        return 1
    return 0


def _shortlist_build(session: Session) -> int:
    path = write_shortlist(session, utcnow())
    print(f"Wrote {path}. Open it in your browser and bookmark it.")
    return 0


def _sources_list(session: Session) -> int:
    sources = list_sources(session)
    for source in sources:
        print(f"{source.id}\t{source.status}\t{source.kind}\t{source.name}\t{source.url}")
    if not sources:
        print("No sources yet.")
    return 0


def _sources_add(
    session: Session, kind: str, name: str, url: str, options: list[str] | None = None
) -> int:
    # A board's name is the last part of its address (jobs.lever.co/acme, jobs.ashbyhq.com/acme).
    config = {"board": urlparse(url).path.strip("/").split("/")[-1]} if kind in BOARD_KINDS else {}
    pairs = options or []
    for flag, value in zip(pairs[::2], pairs[1::2], strict=True):
        if kind != "themuse" or flag not in ("--category", "--level", "--location", "--pages"):
            print(f"Option {flag!r} is not valid for {kind}.")
            return 2
        config[flag[2:]] = value
    source = propose_source(session, kind, name, url, config)
    print(
        f"Source {source.id} is {source.status}. Approve it with: hub sources approve {source.id}"
    )
    return 0


def _ingest(
    session: Session, board: str, client: httpx.Client | None = None, kind: str = "greenhouse"
) -> int:
    """Fetch one approved source of `kind` (greenhouse, lever, or ashby) by its board name."""
    sources = [
        s for s in list_sources(session) if s.kind == kind and s.config.get("board") == board
    ]
    if not sources:
        print(
            f"No {kind} source for {board!r}. Add one with: "
            f"hub sources add {kind} <name> <the board's address>"
        )
        return 1
    try:
        report = ADAPTERS[kind](session, sources[0], client, _aliases())
    except SourceNotApproved as error:
        print(error)
        return 1
    except FetchError as error:
        print(f"Fetch failed; nothing changed. {error}")
        return 1
    print(
        f"{report.new} new, {report.seen_again} seen again, {report.reopened} reopened, "
        f"{report.closed} closed, {report.duplicates} duplicates of other sources."
    )
    return 0


_DECISIONS = {"approve": approve_source, "reject": reject_source, "disable": disable_source}


def _sources_decide(session: Session, action: str, source_id: int) -> int:
    try:
        source = _DECISIONS[action](session, source_id)
    except UnknownSource:
        print(f"No source with id {source_id}.")
        return 1
    print(f"Source {source.id} is now {source.status}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
