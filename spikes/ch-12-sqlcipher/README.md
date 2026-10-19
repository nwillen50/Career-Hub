# Spike CH-12: SQLCipher with SQLAlchemy and Alembic

**Question:** Can Career Hub use an encrypted SQLite database with SQLAlchemy and Alembic on a Mac,
with no compiler or Homebrew?

**Answer:** Yes. See [ADR-0001](../../docs/decisions/0001-encrypted-storage.md).

Each test in `test_sqlcipher_spike.py` answers one question. Run them with:

```bash
uv run pytest spikes/ch-12-sqlcipher -s
```

`-s` shows the backup comparison and SQLCipher's own log lines from the wrong-key test, which are
expected.

Spike code is never imported by the application. The production version of this pattern lives in
`careerhub.core.storage`.
