# `careerhub.core`

The bottom layer. Every other package depends on it; it depends on nothing else in Career Hub.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `paths` | Where the data folder and database file live | CH-37 | Built |
| `storage` | Opens the encrypted database; the only code that does | CH-37 | Built |
| `secrets` | Creates the database key once and keeps it in the macOS Keychain | CH-36 | Built |
| `models` | The shared core entities as typed models, plus the first migration | CH-38 | Next |
| `settings` | Loads user settings from the data folder | CH-15 | Planned |
| `logging` | Logs with personal details redacted | CH-15 | Planned |
| `runlog` | Records each pipeline run | CH-15 | Planned |
| `backup` | Nightly encrypted backup with `VACUUM INTO` | CH-15 | Planned |

Design decisions: [ADR-0001, encrypted storage](../../../docs/decisions/0001-encrypted-storage.md).

## `secrets` in brief

- `get_or_create_database_key(store)` returns the 64-character database key, creating it on first
  use. If a stored key is malformed it raises `InvalidKeyError` rather than replacing it, because a
  new key couldn't open the existing database.
- `KeychainKeyStore` is the real store. `InMemoryKeyStore` is for tests.
- Keys never appear in a store's description, an error message, or a traceback.
- The real-Keychain test is skipped by default. On a Mac, run
  `CAREERHUB_TEST_REAL_KEYCHAIN=1 uv run pytest tests/core/test_secrets_real_keychain.py`.

## `storage` in brief

- `open_database(store)` opens `careerhub.db` in the data folder, creating the key and the file on
  first use. `open_engine(path, key)` opens a specific file with a specific key.
- Every connection sets the key, turns on foreign keys, WAL journaling, and a 5-second busy
  timeout, and silences SQLCipher's own logging.
- A wrong key raises `WrongKeyError` immediately, never on a later query.
- The database and SQLite's `-wal` and `-shm` files are readable only by you (`rw-------`); the
  data folder is `rwx------`.
- `session_factory(engine)` gives ORM sessions.
- The data folder is `~/Library/Application Support/CareerHub` on a Mac. Set
  `CAREERHUB_DATA_DIR` to use another folder.
