# 0002. Data models and migrations

- Status: Accepted
- Date: 2026-10-19
- Ticket: CH-38, for epic CH-11 (step 1)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

The PRD lists fourteen entities and says to define them "as Pydantic models in `core/`", with
schema changes going through Alembic migrations. ADR-0001 settled on SQLAlchemy and Alembic over
an encrypted SQLite database. Three questions were left: what the models are written in, how
times are stored, and when each table gets created.

## Decision

1. **Tables are SQLAlchemy 2.x typed ORM models** (`Mapped[...]`, `mapped_column`), all
   subclassing `careerhub.core.models.Base`. **Pydantic is used at the edges**: validating model
   (LLM) output, settings, and files the user edits. This replaces the PRD's wording "Pydantic
   models in `core/`" for tables: one definition per table, written in the library that stores it.
2. **Every time is UTC with a timezone attached.** The `UTCDateTime` column type stores UTC and
   returns timezone-aware values. Writing a datetime with no timezone is an error, not a guess.
   `utcnow()` is the one way to get "now".
3. **Constraints follow a naming convention** (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`), because SQLite
   migrations rebuild tables and can only drop or change a constraint they can name.
4. **Each table is created by the build step that first needs it.** Step 1 creates the shared core
   tables (CH-40). Job, Assessment, Application, and the other Jobs tables arrive with steps 4
   to 8. This is the bottom-up rule applied to the schema.
5. **Migrations run through `careerhub.core.migrate`**, never from an `alembic.ini`. That module
   supplies the encrypted connection; `env.py` refuses to run without one, so no tool can open the
   database unencrypted. `migrate(engine)` runs at every start and does nothing when current.
6. **New migrations are generated, then read by a person.** `new_revision()` compares the models
   with a throwaway encrypted database at the latest migration and writes the difference. Ruff
   fixes and formats the file as it's written, so it passes CI unedited.

## Evidence

- `tests/core/test_models_base.py`: UTC round trip from another timezone, naive datetimes
  refused, `created_at` kept and `updated_at` moved on change, constraint names as expected.
- `tests/core/test_migrate.py`: migrate brings a new database to the latest revision and is safe
  to rerun; a generated migration is written, applied, and passes Ruff lint and format checks;
  Alembic refuses to run without the encrypted connection.

## Alternatives considered

- **SQLModel** (one class that is both a Pydantic model and a table). Fewer classes, but it lags
  SQLAlchemy releases, and its mix of two libraries' rules is harder to read and debug than either
  library alone.
- **Pydantic models plus separate SQLAlchemy tables for everything.** Two definitions of every
  entity that must be kept in sync by hand; no benefit while the tables are only used inside the
  app.
- **Store times as local time or ISO strings.** Local time is ambiguous across daylight saving
  changes; strings lose type checking and sort correctly only by accident.
- **Create all fourteen tables now.** Building ahead of need: their columns would be guesses that
  later steps have to migrate away from.

## Consequences

- The PRD's data model table stays the source of truth for *what* each entity holds; this ADR
  governs *how* it's built. A PRD wording update is proposed to Nicholas as a ticket.
- Every model module must be imported by `careerhub.core.models` (or the module's own models
  package, once Jobs adds one) so migrations can see it.
- Merged migrations are never edited; changes are new migrations.
