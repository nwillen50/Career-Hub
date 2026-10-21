"""The `hub` command. For now: `hub profile show`."""

from __future__ import annotations

import sys

from careerhub.core.migrate import migrate
from careerhub.core.secrets import KeychainKeyStore, KeyStore, SecretsError
from careerhub.core.storage import StorageError, open_database, session_factory
from careerhub.profile.files import ProfileFileError
from careerhub.profile.profile import Profile, format_profile, load_profile

USAGE = "Usage: hub profile show"


def main(argv: list[str] | None = None, store: KeyStore | None = None) -> int:
    """Run the command. `store` is the database key store (tests pass an in-memory one)."""
    args = sys.argv[1:] if argv is None else argv
    if args != ["profile", "show"]:
        print(USAGE)
        return 2
    try:
        profile = _load(store or KeychainKeyStore())
    except ProfileFileError as error:
        print(f"Problem with a profile file: {error}")
        return 1
    print(format_profile(profile))
    return 0


def _load(store: KeyStore) -> Profile:
    """Load the profile, with the connection count if the database can be opened."""
    try:
        engine = open_database(store)
    except (StorageError, SecretsError):
        return load_profile()  # files only; the count shows as unknown
    migrate(engine)
    with session_factory(engine)() as session:
        return load_profile(session)


if __name__ == "__main__":
    sys.exit(main())
