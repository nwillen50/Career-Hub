# 0004. Core tables follow the PRD's data model

- Status: Accepted
- Date: 2026-10-20
- Ticket: CH-47, for epic CH-11 (step 1)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

CH-40 built Company, Person, Interaction, Source, Proposal, and RunLog before the PRD was in the
repo. Comparing them with the PRD's "Core data model" table found missing fields (company size,
industry, ATS board IDs; how you know a person and last contact; interaction follow-up due; source
configuration, last fetch, health; run cost; proposal origin, target, diff) and one wrong word
(proposals end `rejected`, not `dismissed`).

## Decision

1. **One new migration** (`core tables match prd`); the merged first migration is untouched.
2. **List-like fields are JSON columns**: `Company.domains` (list), `Company.ats_board_ids` and
   `Source.config` (dicts). They are read whole and never queried by key. `Company.domain`
   becomes `domains`, and the migration carries existing values over.
3. **Fixed word sets get CHECK constraints**, like the existing status columns: `Source.health`
   (`unknown`, `ok`, `failing`), `Proposal.origin` (`conversation`, `agent`), `Proposal.target`
   (`preferences`, `rubric`, `source_list`). `Proposal.origin` and `target` are nullable so rows
   made before this migration stay valid.
4. **Proposal status uses the PRD's `rejected`**; existing `dismissed` rows are converted.
5. **`RunLog.cost_usd` is a float.** Sums of a few thousand model calls do not need exact decimal
   arithmetic, and SQLite has no decimal type.
6. Proposal keeps `kind`, `title`, and `body`, which the PRD does not list, because later steps
   need a short label to show.

## Evidence

`tests/core/test_migrations.py`: every new field round-trips; the new constraints reject bad
words; a database built at the first revision, with rows, upgrades with `domain` moved into
`domains` and `dismissed` turned into `rejected`; downgrade restores the first revision.

## Alternatives considered

- **Separate tables for domains and ATS boards.** Cleaner to query, but nothing queries them yet;
  add them when a step needs a lookup (bottom-up rule).
- **Integer micro-dollars for cost.** Exact, but awkward to read and the router has no such need.

## Consequences

CH-51 (cost log) fills `run_log.cost_usd`. The Profile and Jobs tables the PRD also lists arrive
with the steps that need them (ADR-0002).
