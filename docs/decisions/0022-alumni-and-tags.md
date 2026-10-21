# 0022. Alumni lists and tagging people

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-72, part of CH-28 (step 7)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

LinkedIn's export lists company and title but not schools, so the PRD sources alumni from "lists
the user provides" and from "connections the user tags". Scraping school directories is excluded.

## Decision

1. **`hub people import-alumni <file.csv>`** reads a user-provided CSV: required `Name` (or `First
   Name` and `Last Name`), `Company`, `Group` (the school or club); optional `Title`, `Email`, `URL`.
   Wrong columns are an error that names what is needed; rows missing a required value are counted
   and skipped.
2. **Matching an existing person** by profile URL, then email, then name at the same company
   (case-insensitive). A match is never duplicated.
3. **The user's words are never overwritten.** For a known person the alumni tag
   (`alumni: <Group>`) is appended to `how_known`; a new person gets `origin = alumni_list` and the
   tag as `how_known`. Re-importing the same file changes nothing.
4. **`hub people tag <id> <text>`** sets `how_known` (the user's explicit statement, replacing the
   old text; empty clears it). `hub people find <text>` finds ids by name or company.
5. Strength of a relationship is *not* decided here; CH-73 ranks paths from these facts.

## Evidence

`tests/modules/network/test_alumni.py`: create, re-import, tag append that keeps the user's text,
matching by email and URL, skipped rows, column variants, bad files, find and tag, and the commands.

## Alternatives considered

- **A separate `alumni` table.** Alumni are people; a second table would split one network in two.
- **Replace `how_known` on import.** Would erase the user's own notes.

## Consequences

- The tag text is a convention (`alumni: ...`) that CH-73 reads; users can write their own.
- Real alumni lists come from Nicholas (CH-9); terms of each source apply to him, not the code.
