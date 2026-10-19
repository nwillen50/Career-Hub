"""Keys and other secrets, kept in the macOS Keychain.

Career Hub's database is encrypted (see ADR-0001). Its key is 32 random bytes, written as 64
hexadecimal characters. The key is created once, the first time Career Hub runs, and stored in the
Keychain. It never goes into a file, a URL, a log, or the repository.

Code that needs a secret asks a `KeyStore`. The app uses `KeychainKeyStore`; tests use
`InMemoryKeyStore`, so they never touch the real Keychain.

Typical use::

    store = KeychainKeyStore()
    key = get_or_create_database_key(store)
"""

from __future__ import annotations

import re
import secrets
from typing import Protocol

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

#: The Keychain "service" every Career Hub secret is filed under.
SERVICE_NAME = "CareerHub"

#: The Keychain "account" name for the database key.
DATABASE_KEY_NAME = "database-key"

_KEY_BYTES = 32
_HEX_KEY = re.compile(r"[0-9a-f]{64}")


class SecretsError(Exception):
    """Base class for problems with secrets."""


class KeyStoreUnavailableError(SecretsError):
    """The key store can't be reached (for example, no Keychain on this machine)."""


class InvalidKeyError(SecretsError):
    """A stored key exists but isn't a valid database key.

    Career Hub refuses to replace it: a new key could not open the existing database, so
    replacing it silently would lock the user out of their own data.
    """


class KeyStore(Protocol):
    """Somewhere to keep named secrets. Values are strings."""

    def get(self, name: str) -> str | None:
        """Return the secret called `name`, or None if there isn't one."""
        ...

    def set(self, name: str, value: str) -> None:
        """Store `value` as the secret called `name`, replacing any existing value."""
        ...


class KeychainKeyStore:
    """A `KeyStore` backed by the system keychain (the macOS Keychain on a Mac).

    It uses the `keyring` library, which picks the right keychain for the operating system.
    Pass `backend` only in tests, to use a fake keychain instead of the real one.
    """

    def __init__(self, service: str = SERVICE_NAME, backend: KeyringBackend | None = None) -> None:
        """Create a store whose secrets are filed under `service`."""
        self._service = service
        self._backend = backend

    def __repr__(self) -> str:
        """Describe the store without revealing any secret."""
        return f"KeychainKeyStore(service={self._service!r})"

    def get(self, name: str) -> str | None:
        """Return the secret called `name`, or None if there isn't one."""
        try:
            if self._backend is not None:
                return self._backend.get_password(self._service, name)
            return keyring.get_password(self._service, name)
        except keyring.errors.KeyringError as error:
            raise KeyStoreUnavailableError(_unavailable_message(error)) from None

    def set(self, name: str, value: str) -> None:
        """Store `value` as the secret called `name`."""
        try:
            if self._backend is not None:
                self._backend.set_password(self._service, name, value)
            else:
                keyring.set_password(self._service, name, value)
        except keyring.errors.KeyringError as error:
            # "from None" drops the original exception, whose message could contain the secret
            # being stored. Only its type name is kept, in our own message.
            raise KeyStoreUnavailableError(_unavailable_message(error)) from None


class InMemoryKeyStore:
    """A `KeyStore` that lives only in memory. For tests; secrets vanish when it does."""

    def __init__(self) -> None:
        """Create an empty store."""
        self._secrets: dict[str, str] = {}

    def __repr__(self) -> str:
        """Describe the store without revealing any secret."""
        return f"InMemoryKeyStore(names={sorted(self._secrets)!r})"

    def get(self, name: str) -> str | None:
        """Return the secret called `name`, or None if there isn't one."""
        return self._secrets.get(name)

    def set(self, name: str, value: str) -> None:
        """Store `value` as the secret called `name`."""
        self._secrets[name] = value


def new_database_key() -> str:
    """Return a new random database key: 32 bytes as 64 lowercase hex characters."""
    return secrets.token_hex(_KEY_BYTES)


def is_valid_database_key(value: str) -> bool:
    """Return True if `value` looks like a database key (64 lowercase hex characters)."""
    return _HEX_KEY.fullmatch(value) is not None


def get_or_create_database_key(store: KeyStore) -> str:
    """Return the database key, creating and storing one on first use.

    Raises:
        InvalidKeyError: a key is stored but is malformed. It is never replaced automatically.
        KeyStoreUnavailableError: the key store can't be reached.
    """
    existing = store.get(DATABASE_KEY_NAME)
    if existing is not None:
        if not is_valid_database_key(existing):
            raise InvalidKeyError(
                f"The stored {DATABASE_KEY_NAME!r} under {SERVICE_NAME!r} is not a valid "
                "database key. Career Hub will not replace it, because a new key could not "
                "open the existing database."
            )
        return existing

    key = new_database_key()
    store.set(DATABASE_KEY_NAME, key)
    # Read it back so a store that silently drops writes is caught now, not when the
    # database can't be opened later.
    if store.get(DATABASE_KEY_NAME) != key:
        raise KeyStoreUnavailableError("The key store did not keep the new database key.")
    return key


def _unavailable_message(error: Exception) -> str:
    """Explain a keychain failure without including any secret."""
    return (
        f"Couldn't reach the keychain ({type(error).__name__}). On a Mac, check that you're "
        "signed in and the login keychain is unlocked."
    )
