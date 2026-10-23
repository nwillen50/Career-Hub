# How we work

Career Hub is built by Nicholas (product owner) with three personas played by Claude:
**Priya** (product), **Sam** (back end and platform), and **Leo** (front end). The full roles are in
the PRD's Team tab.

## From ticket to merge

1. **Every change starts from a ticket** on the project board (`CH-n`). If there is no ticket, make
   one first.
2. **Branch** from `main` as `ch-<n>-short-description`, for example `ch-12-sqlcipher-spike`.
3. **Commit** in small steps. Start each message with the ticket: `CH-12: test Alembic migrations`.
4. **Open a pull request into `main`** (never into another PR's branch) whose title starts with the ticket and whose description says what
   changed, how it was tested, and anything a reviewer should look at closely.
5. **Review.** A second persona reviews; the owner never reviews their own ticket. Then Nicholas
   approves and merges. Nothing reaches `main` without Nicholas.
6. **Update the board.** Move the ticket and write a one-line note on what was done.

## Definition of done

- [ ] Code merged into `main`
- [ ] Tests pass on Linux and macOS in CI
- [ ] Lint, format, and type checks pass
- [ ] Every public function has a docstring; the module's README is current
- [ ] Reviewed by a second persona and approved by Nicholas
- [ ] Ticket updated on the board

## Release checklist

- [ ] Restore a backup once: `hub backup restore <file> --to <new folder>` must report that the
      integrity check passed and the row counts match (PRD: backups are tested once per release).

## Spikes and decisions

A **spike** is a short experiment that answers one question before we depend on a tool. Each epic
starts with one. Spikes live in `spikes/<ticket>-<topic>/` as runnable tests, and their conclusion
becomes an **architecture decision record** in `docs/decisions/`. Spike code is never imported by the
application.

## Bugs

A bug ticket records how to reproduce it, its severity, and the ticket it came from. Every fix
includes a test that failed before the fix.

## Rules that are never broken

- No personal data, keys, or tokens in the repository, in tests, or in logs.
- No code path that sends, submits, deletes, or applies on the user's behalf.
- No model call outside the model router (`llm/`, from step 2 on).
