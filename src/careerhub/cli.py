"""The `hub` command: `hub profile show` and `hub sources ...`."""

from __future__ import annotations

import getpass
import logging
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from careerhub.core.companies import decide_merge, find_companies, pending_merges, suggest_merges
from careerhub.core.migrate import migrate
from careerhub.core.models import utcnow
from careerhub.core.secrets import KeychainKeyStore, KeyStore, SecretsError
from careerhub.core.settings import SettingsError, load_settings
from careerhub.core.storage import StorageError, open_database, session_factory
from careerhub.llm.cache import ResponseCache
from careerhub.llm.commands import (
    models_add,
    models_add_custom,
    models_list,
    models_remove,
    models_routes,
)
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
from careerhub.modules.jobs.find_boards import find_boards
from careerhub.modules.jobs.manual import ManualJobError, add_job, classify, guess_from_link
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
from careerhub.modules.plan import suggest_week
from careerhub.modules.plan.ics import IcsError
from careerhub.pipeline import run_nightly
from careerhub.power import read_power_source
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import UnknownExtraction, decide, facts_text, pending
from careerhub.profile.files import ProfileFileError, load_preferences, load_profile_doc
from careerhub.profile.models import PlanSettings, Preferences, ProfileDoc
from careerhub.profile.profile import Profile, format_profile, load_profile
from careerhub.profile.readiness import current_readiness, missing_items, set_scoring
from careerhub.schedule import install_plist, launchctl_commands
from careerhub.web.shortlist import write_shortlist

USAGE = """Usage:
  hub profile show
  hub ingest greenhouse|lever|ashby --company <board>   (the source must be approved)
  hub plan                                   (suggested blocks for the next 7 days)
  hub paths <company>                        (who you know there, strongest first)
  hub people find <text>                     (by name or company)
  hub people tag <id> [--school 'Tufts University'] <how you know them>
  hub people import-alumni <file> [--group G] [--sheet S] [--map field=Header] [--preview]
                                             (CSV, TSV or Excel; also `hub alumni import`)
  hub schedule install                       (write the nightly launchd job; prints how to start it)
  hub nightly [--if-plugged-in]              (run the whole pipeline once; skip on battery)
  hub add-job <url> [--company C] [--title T] [--text-file F]   (any link; LinkedIn and Handshake
                                             are never requested; text can also come from stdin)
  hub companies suggestions | merge <id> | skip <id>   (near-duplicate company names)
  hub targets find-boards | note <name> <ats> | import <file> | list | enable-matching
  hub targets enable <name>... | disable <name>...
                          (companies you follow; the Engine list is in docs/seeds)
  hub models list | routes
  hub models add <anthropic|openai|gemini --tier free|paid|local|preset>   (asks for the key)
  hub models remove <same names>
  hub models add-custom <name> <kind> <model> [--base-url U] [--input N] [--output N]
                        [--trains-on-data]
  hub cache clear [--expired]               (delete saved model answers; prints how many)
  hub profile ready [--off]                 (check the profile, then switch scoring on or off)
  hub profile facts                         (what was read from your resume and transcript)
  hub profile facts accept <number|all> | reject <number>
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
            return 0
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
        if args == ["schedule", "install"]:
            return _schedule_install()
        if args[:1] == ["nightly"] and args[1:] in ([], ["--if-plugged-in"]):
            return _nightly(store, if_plugged_in=bool(args[1:]))
        if args[:1] == ["add-job"] and len(args) >= 2 and len(args) % 2 == 0:
            return _with_session(store, lambda s: _add_job(s, args[1], args[2:]))
        if args[:1] == ["companies"]:
            return _with_session(store, lambda s: _companies(s, args[1:]))
        if args == ["targets", "find-boards"]:
            return _with_session(store, _find_boards)
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
    except ProfileFileError as error:
        print(error)
        return 1
    missing = f" (missing {' or '.join(report.skipped_for)})" if report.skipped_for else ""
    print(
        f"{report.created} added, {report.tagged} tagged as alumni, {report.unchanged} unchanged, "
        f"{report.skipped} rows skipped{missing}."
    )
    return 0


def _schedule_install() -> int:
    """Write the launchd job at the `nightly_run_time` setting; print how to start and stop it."""
    settings = load_settings()
    path = install_plist(settings.nightly_run_time, require_power=settings.nightly_require_power)
    start, stop = launchctl_commands(path)
    print(
        f"Wrote {path}. It is not running yet. To start it:\n  {start}\nTo stop it later:\n  {stop}"
    )
    return 0


def _nightly(
    store: KeyStore, if_plugged_in: bool = False, power: Callable[[], str] = read_power_source
) -> int:
    """Run the pipeline once; exit code 1 if the run failed outright.

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
    report = run_nightly(engine, store)
    print(report.summary())
    return 1 if any(p.startswith("run failed") for p in report.problems) else 0


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
            result = add_job(session, url, company, title, None, client)
        except ManualJobError:
            if "--text-file" in flags:
                text = Path(flags["--text-file"]).read_text(encoding="utf-8")
            elif not stdin.isatty():
                text = stdin.read()
            result = add_job(session, url, company, title, text, client)
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
        if args[i] == "--trains-on-data":
            flags["trains_on_data"] = True
        elif args[i].startswith("--"):
            name = args[i][2:].replace("-", "_")
            # An unknown flag is an error: dropping a mistyped --trains-on-data would silently
            # save the less restrictive value.
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
            print(decide_merge(session, int(args[1]), accept=args[0] == "merge"))
        except LookupError as error:
            print(error)
            return 1
        return 0
    print("Usage: hub companies suggestions | merge <id> | skip <id>")
    return 2


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
