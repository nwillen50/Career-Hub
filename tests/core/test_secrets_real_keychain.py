"""The one test that uses the real macOS Keychain.

It's skipped unless you run it on a Mac with the switch turned on:

    CAREERHUB_TEST_REAL_KEYCHAIN=1 uv run pytest tests/core/test_secrets_real_keychain.py

It writes one entry under a throwaway service name, reads it back, and removes it. macOS may ask
you to allow access the first time.
"""

from __future__ import annotations

import os
import sys
import uuid

import keyring
import pytest

from careerhub.core.secrets import KeychainKeyStore, get_or_create_database_key

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("CAREERHUB_TEST_REAL_KEYCHAIN") != "1",
    reason="real Keychain test: run on a Mac with CAREERHUB_TEST_REAL_KEYCHAIN=1",
)


def test_database_key_round_trips_through_the_real_keychain() -> None:
    service = f"CareerHub-test-{uuid.uuid4().hex[:8]}"
    store = KeychainKeyStore(service=service)
    try:
        key = get_or_create_database_key(store)
        assert get_or_create_database_key(KeychainKeyStore(service=service)) == key
    finally:
        keyring.delete_password(service, "database-key")
