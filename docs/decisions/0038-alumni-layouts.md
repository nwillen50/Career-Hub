# 0038. Reading alumni lists in any layout

- Status: Accepted
- Date: 2026-10-22
- Ticket: CH-100 (part 1), for epic CH-28 (step 7)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Nicholas does not yet know what alumni exports look like, so the importer must not depend on exact
column names, one delimiter, or a Group on every row (ADR-0022 required all three).

## Decision

1. **`network/layouts.py`** reads CSV (delimiter sniffed among comma, semicolon, tab, pipe), TSV, and
   Excel `.xlsx` (first sheet, or `--sheet`), handles a BOM and Windows-1252 text, and finds the header
   row as the first of the first 30 rows with at least two recognized column names.
2. **Columns match by synonym**, ignoring case, spacing, and punctuation. The table `SYNONYMS` is one
   data structure covering name, first/last, company, title, email, URL, group, class year, major, and
   location; `--map field='Header'` overrides one mapping. Only name (or first and last) and company are
   required.
3. **The group** comes from a Group column or `--group` for the whole file. Class year, major, and
   location go in a *new* person's notes; an existing person's notes are never changed.
4. **`--preview`** prints the header row, how each column was mapped or ignored, and counts of people
   that would be added, tagged, left, or skipped. It saves nothing (the import runs and is rolled back)
   and prints no names, companies, or other details.
5. Companies use the shared `company_key` (ADR-0033). `openpyxl` (MIT) is a new dependency.
6. `hub alumni import` is an alias of `hub people import-alumni`.

Not in this change: remembering the mapping per file name (CH-116).

## Evidence

`tests/modules/network/test_layouts.py`.

## Consequences

Excel files are read as values only (`data_only=True`), never formulas.
