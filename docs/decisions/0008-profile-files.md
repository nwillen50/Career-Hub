# 0008. Profile file formats

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-55, part of CH-19 (epic CH-18, step 3)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

The PRD says the profile comes from "profile and preference files with hard constraints", edited
by the user, and that a missing or malformed file must be reported clearly.

## Decision

1. **`profile/profile.md`**: YAML front matter for structured fields (name, education, skills,
   interests), the Markdown body kept as free text (`about`). **`profile/preferences.yaml`**:
   hard constraints, timeline, weekly time budget. Both in the data folder, never the repo.
2. **Pydantic models reject unknown keys** and are frozen, so a typo is an error.
3. **Missing file = `None`; malformed file = `ProfileFileError`.** Errors name fields and rules,
   never values (values are personal). YAML is parsed with `safe_load`.
4. **Hard constraints** in v1: locations, remote_ok, min_salary_usd, excluded_companies,
   excluded_title_words. More can be added when the filters (step 6) need them.
5. Adds the `pyyaml` dependency (and `types-pyyaml` for the type checker).

## Evidence

`tests/profile/test_files.py`: parsing of both files with made-up samples, defaults, missing
files, and error messages that omit values.

## Alternatives considered

- **TOML or JSON.** Neither takes a free-text body the way Markdown front matter does; YAML is
  what the PRD names for `preferences.yaml`.
- **One file for everything.** Preferences change more often and feed the Conversation panel's
  proposals as diffs; a separate file keeps those diffs small.

## Consequences

- The Conversation panel (step 9) will propose edits to `preferences.yaml` as text diffs.
- The exact constraint list is a first guess; Nicholas can name more.
