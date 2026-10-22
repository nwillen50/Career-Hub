"""The `hub` command: `hub profile show` and `hub sources ...`."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import utcnow
from careerhub.core.secrets import KeychainKeyStore, KeyStore, SecretsError
from careerhub.core.settings import SettingsError, load_settings
from careerhub.core.storage import StorageError, open_database, session_factory
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
    find_company,
    find_people,
    import_alumni,
    tag_person,
    warm_paths,
)
from careerhub.modules.plan import suggest_week
from careerhub.modules.plan.ics import IcsError
from careerhub.pipeline import run_nightly
from careerhub.profile.files import ProfileFileError, load_preferences, load_profile_doc
from careerhub.profile.profile import Profile, format_profile, load_profile
from careerhub.schedule import install_plist, launchctl_commands
from careerhub.web.shortlist import write_shortlist

USAGE = """Usage:
  hub profile show
  hub ingest greenhouse|lever|ashby --company <board>   (the source must be approved)
  hub plan                                   (suggested blocks for the next 7 days)
  hub paths <company>                        (who you know there, strongest first)
  hub people find <text>                     (by name or company)
  hub people tag <id> <how you know them>
  hub people import-alumni <file.csv>
  hub schedule install                       (write the nightly launchd job; prints how to start it)
  hub nightly                                (run the whole pipeline once)
  hub add-job <url> [--company C] [--title T] [--text-file F]   (any link; LinkedIn and Handshake
                                             are never requested; text can also come from stdin)
  hub targets find-boards | note <name> <ats> | import <file> | list | enable-matching
  hub targets enable <name>... | disable <name>...
                          (companies you follow; the Engine list is in docs/seeds)
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
        if args == ["profile", "show"]:
            print(format_profile(_load_profile(store)))
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
            return _with_session(store, lambda s: _people_tag(s, int(args[2]), " ".join(args[3:])))
        if args[:2] == ["people", "import-alumni"] and len(args) == 3:
            return _with_session(store, lambda s: _people_import(s, Path(args[2])))
        if args == ["schedule", "install"]:
            return _schedule_install()
        if args == ["nightly"]:
            return _nightly(store)
        if args[:1] == ["add-job"] and len(args) >= 2 and len(args) % 2 == 0:
            return _with_session(store, lambda s: _add_job(s, args[1], args[2:]))
        if args == ["targets", "find-boards"]:
            return _with_session(store, _find_boards)
        if args[:1] == ["targets"] and len(args) >= 2:
            return _targets(args[1:])
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


def _plan(session: Session) -> int:
    """Print the suggested week. It only suggests: nothing is written to a calendar."""
    settings = load_settings()
    preferences = load_preferences()
    budget = preferences.weekly_time_budget_hours if preferences else 5.0
    try:
        plan = suggest_week(session, settings, budget, utcnow())
    except IcsError as error:
        print(f"Can't read a calendar file: {error}")
        return 1
    zone = ZoneInfo(settings.timezone)
    hours, minutes = divmod(plan.planned_minutes, 60)
    print(f"Suggested plan ({hours}h{minutes:02d}m of {budget:g}h):")
    for block in plan.blocks:
        when = block.start.astimezone(zone)
        print(f"  {when:%a %b %d %H:%M}-{block.end.astimezone(zone):%H:%M}  {block.task.title}")
    for item in plan.unplaced:
        print(f"  not placed: {item.task.title} ({item.reason})")
    if not plan.blocks and not plan.unplaced:
        print("  Nothing to plan yet: no ranked jobs. Run `hub nightly` first.")
    return 0


def _paths(session: Session, company_text: str) -> int:
    company = find_company(session, company_text)
    if company is None:
        print(f"No company matching {company_text!r}.")
        return 1
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


def _people_tag(session: Session, person_id: int, how_known: str) -> int:
    try:
        person = tag_person(session, person_id, how_known)
    except UnknownPerson:
        print(f"No person with id {person_id}.")
        return 1
    print(f"{person.full_name}: {person.how_known or '(cleared)'}")
    return 0


def _people_import(session: Session, path: Path) -> int:
    report = import_alumni(session, path)
    print(
        f"{report.created} added, {report.tagged} tagged as alumni, {report.unchanged} unchanged, "
        f"{report.skipped} rows skipped (missing name, company, or group)."
    )
    return 0


def _schedule_install() -> int:
    """Write the launchd job at the `nightly_run_time` setting; print how to start and stop it."""
    path = install_plist(load_settings().nightly_run_time)
    start, stop = launchctl_commands(path)
    print(
        f"Wrote {path}. It is not running yet. To start it:\n  {start}\nTo stop it later:\n  {stop}"
    )
    return 0


def _nightly(store: KeyStore) -> int:
    """Run the pipeline once; exit code 1 if the run failed outright."""
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
        report = ADAPTERS[kind](session, sources[0], client)
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
