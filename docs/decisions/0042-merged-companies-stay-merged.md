# 0042. A merged company's old name keeps finding the company

- Status: Accepted
- Date: 2026-10-23
- Ticket: CH-154 (found by the CH-131 review), for epic CH-21 (step 4)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

ADR-0041 lets the user accept a suggested merge ("Acme Robotics" into "Acme"). The merge moved jobs
and people and deleted the dropped company, but nothing remembered the dropped name. The next
ingest or alumni import under that name created the company again, the suggestion was not repeated
(it was already accepted), and jobs and people split again.

## Decision

1. A merge saves the dropped company's key (and any keys merged into it earlier) on the kept company
   as `merged_keys` (new nullable JSON column, migration `a154c0de0001`). `get_or_create_company` and
   `find_companies` look at `merged_keys` after the exact key.
2. These are stored in the database, not in `preferences.yaml`, because the user's file is theirs and
   Career Hub never rewrites it. A user alias still wins, since aliases are applied before lookup.
3. Moved jobs get new duplicate keys, and postings of one job from two sources are linked to the
   earliest (`link_duplicates`), so the shortlist doesn't show both.
4. A suggestion records the keys it was made for and refuses to merge if the companies at those ids
   no longer have them (SQLite can reuse ids).
5. Not changed: `hub companies merge <id>` is the user's explicit command, so it asks no further yes/no.
   A merge still cannot be undone; the dropped name's key is what remains.

## Evidence

`tests/core/test_company_merge_holds.py`.

## Consequences

One extra query per company lookup that misses the exact key. Flagged for Nicholas: whether a merge
should be undoable (it would need to keep the dropped company's own jobs list).
