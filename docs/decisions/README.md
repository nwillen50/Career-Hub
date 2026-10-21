# Architecture decision records

Each file here records one decision: the context, what we chose, what we rejected, and the
consequences. They are numbered in order and never deleted; a reversed decision gets a new record
that supersedes the old one.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-encrypted-storage.md) | Encrypted storage with SQLCipher, SQLAlchemy, and Alembic | Accepted |
| [0002](0002-data-models-and-migrations.md) | Data models in SQLAlchemy, UTC times, tables added by the step that needs them | Accepted |
| [0003](0003-router-contract.md) | Router contract: ordered privacy tiers, one choke point, no fallback on refusal | Accepted |
| [0004](0004-core-tables-match-prd.md) | Core tables follow the PRD's data model; JSON for list fields; proposals end `rejected` | Accepted |
| [0005](0005-response-cache.md) | Response cache keyed by provider, model, and prompt hash; checked after privacy | Accepted |
| [0006](0006-cost-log-and-budget.md) | Cost log per call, monthly budget setting, essential calls exempt | Accepted |
| [0007](0007-real-providers.md) | Real providers as thin httpx adapters; local must be localhost | Accepted |
| [0008](0008-profile-files.md) | Profile files: Markdown front matter and YAML, strict models, errors without values | Accepted |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD
- Ticket: CH-n
- Deciders: who

## Context
What problem, and what constraints.

## Decision
What we chose, stated plainly.

## Evidence
What we tested, and the results.

## Alternatives considered
What else we looked at, and why not.

## Consequences
What this makes easier, harder, or requires next.
```
