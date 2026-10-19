"""Tests for careerhub.core.secrets.

None of these touch the real Keychain. The one test that does is in
test_secrets_real_keychain.py and runs only on a Mac when switched on.
"""

from __future__ import annotations

import traceback

import keyring.errors
import pytest
from keyring.backend import KeyringBackend

from careerhub.core.secrets import (
    DATABASE_KEY_NAME,
    SERVICE_NAME,
    InMemoryKeyStore,
    InvalidKeyError,
    KeychainKeyStore,
    KeyStoreUnavailableError,
    get_or_create_database_key,
    is_valid_database_key,
    new_database_key,
)


class FakeKeyring(KeyringBackend):
    """A keychain stand-in that records what was stored."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.passwords[(service, username)]


class BrokenKeyring(FakeKeyring):
    """A keychain stand-in that fails like a locked or missing keychain."""

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError("locked")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("locked")


class ForgetfulStore(InMemoryKeyStore):
    """A store that accepts writes but keeps nothing."""

    def set(self, name: str, value: str) -> None:
        pass


# --- Keys ---------------------------------------------------------------------


def test_new_key_is_64_lowercase_hex_characters() -> None:
    key = new_database_key()
    assert len(key) == 64
    assert is_valid_database_key(key)


def test_new_keys_are_different_each_time() -> None:
    assert len({new_database_key() for _ in range(20)}) == 20


@pytest.mark.parametrize(
    "value",
    ["", "abc", "g" * 64, "A" * 64, "a" * 63, "a" * 65, " " + "a" * 64, "a" * 64 + "\n"],
)
def test_malformed_keys_are_rejected(value: str) -> None:
    assert not is_valid_database_key(value)


# --- get_or_create_database_key ---------------------------------------------


def test_first_use_creates_and_stores_a_key() -> None:
    store = InMemoryKeyStore()
    key = get_or_create_database_key(store)
    assert is_valid_database_key(key)
    assert store.get(DATABASE_KEY_NAME) == key


def test_later_uses_return_the_same_key() -> None:
    store = InMemoryKeyStore()
    assert get_or_create_database_key(store) == get_or_create_database_key(store)


def test_a_malformed_stored_key_is_never_replaced() -> None:
    store = InMemoryKeyStore()
    store.set(DATABASE_KEY_NAME, "not-a-key")
    with pytest.raises(InvalidKeyError):
        get_or_create_database_key(store)
    assert store.get(DATABASE_KEY_NAME) == "not-a-key"


def test_a_store_that_drops_writes_is_caught() -> None:
    with pytest.raises(KeyStoreUnavailableError):
        get_or_create_database_key(ForgetfulStore())


# --- KeychainKeyStore -------------------------------------------------------


def test_keychain_store_files_secrets_under_the_career_hub_service() -> None:
    fake = FakeKeyring()
    key = get_or_create_database_key(KeychainKeyStore(backend=fake))
    assert fake.passwords == {(SERVICE_NAME, DATABASE_KEY_NAME): key}


def test_keychain_store_reads_back_what_it_wrote() -> None:
    store = KeychainKeyStore(backend=FakeKeyring())
    store.set("example", "value")
    assert store.get("example") == "value"
    assert store.get("missing") is None


def test_keychain_failures_become_a_clear_error() -> None:
    store = KeychainKeyStore(backend=BrokenKeyring())
    with pytest.raises(KeyStoreUnavailableError, match="keychain"):
        store.get(DATABASE_KEY_NAME)
    with pytest.raises(KeyStoreUnavailableError, match="keychain"):
        store.set(DATABASE_KEY_NAME, new_database_key())


def test_stores_never_reveal_secrets_in_their_description() -> None:
    key = new_database_key()
    memory = InMemoryKeyStore()
    memory.set(DATABASE_KEY_NAME, key)
    keychain = KeychainKeyStore(backend=FakeKeyring())
    keychain.set(DATABASE_KEY_NAME, key)
    assert key not in repr(memory)
    assert key not in repr(keychain)


def test_error_messages_never_contain_the_key() -> None:
    key = new_database_key()

    class LeakyBrokenKeyring(BrokenKeyring):
        def set_password(self, service: str, username: str, password: str) -> None:
            raise keyring.errors.PasswordSetError(f"could not store {password}")

    store = KeychainKeyStore(backend=LeakyBrokenKeyring())
    with pytest.raises(KeyStoreUnavailableError) as caught:
        store.set(DATABASE_KEY_NAME, key)
    assert key not in str(caught.value)
    # The full traceback, as a log would print it, must not contain the key either.
    full_traceback = "".join(traceback.format_exception(caught.value))
    assert key not in full_traceback
