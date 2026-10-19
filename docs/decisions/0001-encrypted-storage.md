# 0001. Encrypted storage with SQLCipher, SQLAlchemy, and Alembic

- Status: Accepted
- Date: 2026-10-19
- Ticket: CH-12 (spike), for epic CH-11 (step 1)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Career Hub stores personal data: applications, contacts, interactions, assessments, and, later,
details derived from email. The PRD requires the database to be encrypted at rest, with the key in
the macOS Keychain, and requires schema changes to go through migrations. It also requires the code
to be readable and the setup to be simple for someone cloning the repo.

The open question was whether SQLCipher (an encrypted build of SQLite) works cleanly with SQLAlchemy
and Alembic on macOS, without asking users to compile anything.

## Decision

- Use **SQLCipher** through the **`sqlcipher3-wheels`** package, which ships prebuilt wheels.
- Use **SQLAlchemy 2.x** for data access and **Alembic** for migrations, with Alembic's batch mode
  for column changes (SQLite's way of altering tables).
- Open connections through a small factory that sets the key with `PRAGMA key` on every new
  connection. The key is **32 random bytes passed as a raw key** (`x'<64 hex>'`). It is generated
  once, stored in the Keychain, and never written to a file, a URL, or a log.
- Turn on `foreign_keys` and `WAL` journaling on every connection.
- Back up with **`VACUUM INTO`**, which produces a consistent, encrypted copy while the app runs.
- Rotate keys with **`PRAGMA rekey`** when needed.

## Evidence

The spike lives in [`spikes/ch-12-sqlcipher/`](../../spikes/ch-12-sqlcipher/). Its seven tests all
pass:

| Question | Result |
| --- | --- |
| Does the SQLAlchemy ORM read and write through SQLCipher? | Yes |
| Is the file unreadable as plain SQLite, with no plaintext in it? | Yes. No plaintext header; a test marker string does not appear in the bytes; Python's standard `sqlite3` refuses to open it |
| Does a wrong key fail loudly? | Yes, with "file is not a database" |
| Do Alembic migrations run up and down? | Yes, including batch-mode column changes, and the database stays encrypted afterward |
| Can the key be rotated in place? | Yes, with `PRAGMA rekey` |
| Do backups stay encrypted and restorable? | Yes, both `VACUUM INTO` and a file copy after a WAL checkpoint |
| Does Alembic's Operations API work for one-off changes? | Yes |

Prebuilt wheels exist for Python 3.12 and 3.13 on both Apple silicon (`macosx_11_0_arm64`) and Intel
Macs (`macosx_10_13_x86_64`), so users don't need Homebrew or a compiler.

Tested versions: SQLCipher with SQLite 3.51.1, SQLAlchemy 2.1.5, Alembic 1.20.0, Python 3.13,
on Linux. CI runs the same spike tests on macOS, which closes the gap between the spike machine and
the target platform.

## Alternatives considered

- **Plain SQLite inside an encrypted APFS volume** (the PRD's fallback). Works, but encryption
  depends on the user setting up and mounting a volume, and the database is readable whenever the
  volume is mounted. Kept as a fallback only.
- **Encrypting individual fields in the application.** Leaves table structure and metadata
  readable, and makes searching and sorting encrypted fields hard.
- **`pysqlcipher3`.** Requires building against a system SQLCipher library, which means Homebrew
  and a compiler for every user.
- **Passing the key in the SQLAlchemy URL** (`sqlite+pysqlcipher://:key@/path`). Puts the key in a
  string that is easy to log by accident. Rejected in favor of the connection factory.

## Consequences

- `careerhub.core.storage` (CH-14) implements the connection factory and is the only place that
  opens the database.
- `careerhub.core.secrets` (CH-14) creates and reads the key from the Keychain.
- Migrations must use batch mode for any column change; the migration template sets
  `render_as_batch=True`.
- SQLCipher logs its own error lines when a wrong key is used. They contain no secrets, but the app
  should set `PRAGMA cipher_log_level = NONE` to keep its logs clean. To be confirmed in CH-14.
- Backups (CH-15) use `VACUUM INTO`.
- If `sqlcipher3-wheels` stops publishing wheels for a new Python version, we pin Python to the last
  supported version until it catches up, or fall back to the encrypted volume.
