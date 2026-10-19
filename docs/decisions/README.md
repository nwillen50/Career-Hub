# Architecture decision records

Each file here records one decision: the context, what we chose, what we rejected, and the
consequences. They are numbered in order and never deleted; a reversed decision gets a new record
that supersedes the old one.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-encrypted-storage.md) | Encrypted storage with SQLCipher, SQLAlchemy, and Alembic | Accepted |

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
