# 0041. Company aliases are the user's; near-duplicates are suggestions

- Status: Accepted (suggestion rule flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-111, for epic CH-21 (step 4)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

ADR-0033 made one key per company with no fuzzy matching, so two real companies are never merged by
accident. That leaves names that really are one employer ("Old Name Labs" became "New Name") and
names that might be ("Acme", "Acme Robotics"). Only the user knows which.

## Decision

1. **`company_aliases`** in `preferences.yaml` maps an old name to the name now used. It is empty by
   default. `resolve_aliases` turns names into keys, follows chains (A to B to C), and rejects loops
   and blank names; `Preferences` runs it on load, so a mistake is reported when the file is read.
2. **`company_key(name, aliases)`** applies the aliases last. `get_or_create_company`,
   `find_companies`, `dedupe_key`, `store_jobs`, `import_alumni`, `apply_filters` (excluded
   companies), the nightly run, and `hub paths` pass them. An existing company created before the
   alias was added stays separate until merged with `hub companies`.
3. **Near-miss suggestions**: after a job or alumni import, two companies whose keys are the same
   words plus one or two more at the end become one pending `Proposal` (kind `company_merge`),
   deduplicated by title, including rejected ones. Nothing merges by itself.
4. **`hub companies suggestions | merge <id> | skip <id>`** is how the user answers; merging uses
   `merge_companies`, keeps the shorter name, and repoints jobs and people.

## Consequences

- The rule is deliberately narrow ("Meta" and "Metabase" never match). Different words
  ("Acme" and "Acme Corp Europe") need an alias.
- The suggestion rule can be noisy for short generic names; each is one line the user can skip.
- Adapters added after this (Lever, Ashby, The Muse, manual jobs) must pass `aliases` to
  `store_jobs`; they gain it when their branches merge this one.
