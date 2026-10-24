# `careerhub.core`

The bottom layer. Every other package depends on it; it depends on nothing else in Career Hub.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `paths` | Where the data folder and database file live | CH-37 | Built |
| `storage` | Opens the encrypted database; the only code that does | CH-37 | Built |
| `secrets` | Creates the database key once and keeps it in the macOS Keychain | CH-36 | Built |
| `models` | `Base`, UTC time handling, timestamps (CH-38); the shared core tables (CH-40) | CH-38, CH-40 | Built |
| `migrate` | Brings the database to the latest schema; writes new migration files | CH-38 | Built |
| `settings` | Loads user settings from the data folder | CH-44 | Built |
| `logging` | Logs with personal details redacted | CH-44 | Built |
| `runlog` | Records each pipeline run; closes runs a crash or power loss cut off (CH-164) | CH-45 | Built |
| `backup` | Nightly encrypted backup with `VACUUM INTO`; `hub backup restore` into a new folder (CH-166) | CH-45 | Built |

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

## `settings` and `logging` in brief

- `load_settings()` reads `settings.json` from the data folder; no file means defaults. A bad
  file raises `SettingsError` naming the setting, never echoing its value. Unknown names are
  rejected so typos don't silently do nothing.
- `configure_logging(level, log_file)` sets up the `careerhub` logger. Every message and
  traceback is passed through `redact()`, which hides emails, phone numbers, keys, and
  credentials. It's a safety net: still don't log personal data.
## Core tables

`models/core_tables.py` holds `Company`, `Person`, `Interaction`, `Source`, `Proposal`, and
`RunLog`. Sources and proposals start as `proposed` / `pending` and only the user moves them on.
`tests/core/test_migrations.py` fails if a model changes without a migration.
The columns match the PRD's "Core data model" table (ADR-0004): list-like fields (`Company.domains`,
`ats_board_ids`, `Source.config`) are JSON, and `RunLog.cost_usd` is a float in US dollars.

## `runlog` and `backup` in brief

- `start_run` / `finish_run` record a step in `run_log`. A failed run stores only the error's
  type name, never its message. A run left `running` was interrupted.
- `backup_database(engine, keep)` writes `backups/careerhub-<UTC time>.db`, still encrypted with
  the same key, then removes older backups beyond `keep`. It only removes files matching its own
  naming pattern. Scheduling it nightly belongs to the schedule steps (CH-27, CH-34).
- `restore_backup(file, new_folder, key)` copies a backup into a folder that must not exist yet,
  opens it with the key, runs SQLite's integrity check and compares row counts with the backup.
  It never writes over the live database. `hub backup list` and `hub backup restore` use it.

## `models` and `migrate` in brief

See [ADR-0002](../../../docs/decisions/0002-data-models-and-migrations.md) for the reasoning.

- Every table subclasses `models.Base`. Add `TimestampMixin` for `created_at` and `updated_at`.
- Use `UTCDateTime()` for every time column and `utcnow()` for "now". A time without a timezone
  is refused.
- `migrate(engine)` brings a database up to date; call it at every start.
- To change the schema, edit the models, then run
  `uv run python -m careerhub.core.migrate new "what changed"` and read the file it writes. The
  steps are in [`migrations/README.md`](migrations/README.md).

## Company names (`names.py`, `companies.py`)

`company_key` makes "Microsoft", "Microsoft.com", and "Microsoft Corporation" one key (legal words,
web endings, "the", and punctuation are ignored; a short built-in alias list covers Google/Alphabet
and similar). Every importer uses `get_or_create_company`; lookups use `find_companies`, which
matches whole words only and reports ambiguity. See [ADR-0033](../../../docs/decisions/0033-company-names.md).

Your own aliases (`company_aliases` in `preferences.yaml`) are checked by `resolve_aliases` (chains
followed, loops rejected) and passed as `aliases`. `suggest_merges` records near-duplicates such as
"Acme" and "Acme Robotics" as pending proposals; `hub companies suggestions | merge <id> | skip <id>`
answers them, and nothing merges by itself. See [ADR-0041](../../../docs/decisions/0041-company-aliases-and-suggestions.md).
