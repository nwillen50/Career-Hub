"""The `hub` command: `hub profile show` and `hub sources ...`."""

from __future__ import annotations

import sys
from collections.abc import Callable

from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.secrets import KeychainKeyStore, KeyStore, SecretsError
from careerhub.core.storage import StorageError, open_database, session_factory
from careerhub.modules.jobs import (
    UnknownSource,
    approve_source,
    disable_source,
    list_sources,
    propose_source,
    reject_source,
)
from careerhub.profile.files import ProfileFileError
from careerhub.profile.profile import Profile, format_profile, load_profile

USAGE = """Usage:
  hub profile show
  hub sources list
  hub sources add <kind> <name> <url>     (saved as proposed; nothing is fetched yet)
  hub sources approve|reject|disable <id>"""


def main(argv: list[str] | None = None, store: KeyStore | None = None) -> int:
    """Run the command. `store` is the database key store (tests pass an in-memory one)."""
    args = sys.argv[1:] if argv is None else argv
    store = store or KeychainKeyStore()
    try:
        if args == ["profile", "show"]:
            print(format_profile(_load_profile(store)))
            return 0
        if args[:2] == ["sources", "list"] and len(args) == 2:
            return _with_session(store, _sources_list)
        if args[:2] == ["sources", "add"] and len(args) == 5:
            return _with_session(store, lambda s: _sources_add(s, *args[2:]))
        if len(args) == 3 and args[0] == "sources" and args[1] in _DECISIONS and args[2].isdigit():
            return _with_session(store, lambda s: _sources_decide(s, args[1], int(args[2])))
    except ProfileFileError as error:
        print(f"Problem with a profile file: {error}")
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


def _sources_list(session: Session) -> int:
    sources = list_sources(session)
    for source in sources:
        print(f"{source.id}\t{source.status}\t{source.kind}\t{source.name}\t{source.url}")
    if not sources:
        print("No sources yet.")
    return 0


def _sources_add(session: Session, kind: str, name: str, url: str) -> int:
    source = propose_source(session, kind, name, url)
    print(
        f"Source {source.id} is {source.status}. Approve it with: hub sources approve {source.id}"
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
