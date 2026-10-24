# 0045. A merge is logged so it can be undone exactly

- Status: Accepted
- Date: 2026-10-24
- Ticket: CH-174 (follow-up to CH-154, ADR 0042)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

A merge deletes the dropped company's row and moves its jobs and people. If two companies were
merged by mistake, nothing could say which jobs and people came from where.

## Decision

1. `company.merge_log` (JSON, new column, migration `c174a0de0001`) keeps, per merged company,
   its old row (name, key, domains, boards, size, industry, notes, its own `merged_keys`) and the
   job and person ids that moved. Chains carry the older entries along.
2. `hub companies split <company> <name>` (after a yes/no question) restores that company, moves
   back only the jobs and people still on the kept company, removes the old name from
   `merged_keys`, recomputes duplicate keys and links, and records a rejected merge suggestion so
   the pair is not suggested again. `hub companies merges` lists merges.
3. A merge made before the log existed falls back to the company name the source gave on each job
   (`raw_payload`); its people stay, and the command says so.
4. CH-154 saved no alias in preferences (`merged_keys` is the alias), so there is no user file to
   edit; removing the key from `merged_keys` is the "remove the alias" step.

## Consequences

The Split button on the company view arrives with the interface work. A split after later edits
is exact for what still sits where the merge put it, and leaves anything moved since.
