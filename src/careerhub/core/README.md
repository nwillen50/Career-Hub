# `careerhub.core`

The bottom layer. Every other package depends on it; it depends on nothing else in Career Hub.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `paths` | Where the data folder and database file live | CH-37 | Built |
| `storage` | Opens the encrypted database; the only code that does | CH-37 | Built |
| `secrets` | Creates the database key once and keeps it in the macOS Keychain | CH-36 | Built |
| `models` | `Base`, UTC time handling, timestamps (CH-38); the shared core tables (CH-40) | CH-38, CH-40 | Built |
| `migrate` | Brings the database to the latest schema; writes new migration files | CH-38 | Built |
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

## Core tables

`models/core_tables.py` holds `Company`, `Person`, `Interaction`, `Source`, `Proposal`, and
`RunLog`. Sources and proposals start as `proposed` / `pending` and only the user moves them on.
`tests/core/test_migrations.py` fails if a model changes without a migration.

## `models` and `migrate` in brief

See [ADR-0002](../../../docs/decisions/0002-data-models-and-migrations.md) for the reasoning.

- Every table subclasses `models.Base`. Add `TimestampMixin` for `created_at` and `updated_at`.
- Use `UTCDateTime()` for every time column and `utcnow()` for "now". A time without a timezone
  is refused.
- `migrate(engine)` brings a database up to date; call it at every start.
- To change the schema, edit the models, then run
  `uv run python -m careerhub.core.migrate new "what changed"` and read the file it writes. The
  steps are in [`migrations/README.md`](migrations/README.md).
