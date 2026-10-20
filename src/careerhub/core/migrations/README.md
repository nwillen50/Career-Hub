# Migrations

Every change to the database's structure is a migration file in `versions/`, applied in order.
Career Hub brings the database up to date with `careerhub.core.migrate.migrate(engine)`.

## Making a new migration

1. Change or add models in `careerhub/core/models/` (or a module's own models).
2. Generate the migration from the difference between the models and the current schema:

   ```bash
   uv run python -m careerhub.core.migrate new "add job table"
   ```

   This builds a throwaway encrypted database at the latest migration, compares it with the
   models, and writes a new file in `versions/`.
3. **Read the generated file.** Autogenerate misses some changes (renames look like a drop plus an
   add). Fix it by hand if needed.
4. Run the tests. `tests/core/test_migrations.py` fails if the models and migrations disagree.

## Rules

- Never edit a migration that has been merged into `main`; add a new one.
- Column changes on SQLite use batch mode (`with op.batch_alter_table(...)`), which autogenerate
  produces automatically here.
- Every migration has a working `downgrade()`.
