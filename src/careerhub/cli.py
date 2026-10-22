"""The `hub` command: `hub profile show` and `hub sources ...`."""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

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
    ingest_greenhouse,
    list_sources,
    propose_source,
    reject_source,
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
from careerhub.profile.extract import ProfileFacts
from careerhub.profile.facts import UnknownExtraction, decide, facts_text, pending
from careerhub.profile.files import ProfileFileError, load_preferences
from careerhub.profile.models import ProfileDoc
from careerhub.profile.profile import Profile, format_profile, load_profile
from careerhub.schedule import install_plist, launchctl_commands
from careerhub.web.shortlist import write_shortlist

USAGE = """Usage:
  hub profile show
  hub ingest greenhouse --company <board>   (the source must be approved)
  hub plan                                   (suggested blocks for the next 7 days)
  hub paths <company>                        (who you know there, strongest first)
  hub people find <text>                     (by name or company)
  hub people tag <id> <how you know them>
  hub people import-alumni <file.csv>
  hub schedule install                       (write the nightly launchd job; prints how to start it)
  hub nightly                                (run the whole pipeline once)
  hub models list | routes
  hub models add <anthropic|openai|gemini --tier free|paid|local|preset>   (asks for the key)
  hub models remove <same names>
  hub models add-custom <name> <kind> <model> [--base-url U] [--input N] [--output N]
                        [--trains-on-data]
  hub cache clear [--expired]               (delete saved model answers; prints how many)
  hub profile facts                         (what was read from your resume and transcript)
  hub profile facts accept <number|all> | reject <number>
  hub shortlist build                        (write the shortlist page)
  hub sources list
  hub sources add <kind> <name> <url>     (saved as proposed; nothing is fetched yet)
  hub sources approve|reject|disable <id>"""


def main(argv: list[str] | None = None, store: KeyStore | None = None) -> int:
    """Run the command. `store` is the database key store (tests pass an in-memory one)."""
    args = sys.argv[1:] if argv is None else argv
    store = store or KeychainKeyStore()
    try:
        if args[:2] == ["profile", "facts"]:
            return _with_session(store, lambda s: _profile_facts(s, args[2:]))
        if args == ["profile", "show"]:
            print(format_profile(_load_profile(store)))
            return 0
        if len(args) == 4 and args[:2] == ["ingest", "greenhouse"] and args[2] == "--company":
            return _with_session(store, lambda s: _ingest(s, args[3]))
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
        if args[:1] == ["models"] and len(args) >= 2:
            return _models(store, args[1:])
        if args[:2] == ["cache", "clear"] and args[2:] in ([], ["--expired"]):
            return _cache_clear(store, expired_only=bool(args[2:]))
        if args == ["shortlist", "build"]:
            return _with_session(store, _shortlist_build)
        if args[:2] == ["sources", "list"] and len(args) == 2:
            return _with_session(store, _sources_list)
        if args[:2] == ["sources", "add"] and len(args) == 5:
            return _with_session(store, lambda s: _sources_add(s, *args[2:]))
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


def _sources_add(session: Session, kind: str, name: str, url: str) -> int:
    # A Greenhouse board's name is the last part of its address.
    config = {"board": urlparse(url).path.strip("/").split("/")[-1]} if kind == "greenhouse" else {}
    source = propose_source(session, kind, name, url, config)
    print(
        f"Source {source.id} is {source.status}. Approve it with: hub sources approve {source.id}"
    )
    return 0


def _ingest(session: Session, board: str, client: httpx.Client | None = None) -> int:
    """Fetch one approved Greenhouse source, found by its board name."""
    sources = [
        s
        for s in list_sources(session)
        if s.kind == "greenhouse" and s.config.get("board") == board
    ]
    if not sources:
        print(
            f"No Greenhouse source for {board!r}. Add one with: "
            f"hub sources add greenhouse <name> https://boards.greenhouse.io/{board}"
        )
        return 1
    try:
        report = ingest_greenhouse(session, sources[0], client)
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
