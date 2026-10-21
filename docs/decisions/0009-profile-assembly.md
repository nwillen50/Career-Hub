# 0009. Profile assembly: missing is reported, broken is fatal

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-57, part of CH-19 (epic CH-18, step 3)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Step 3 is done when `hub profile show` prints a parsed profile. The inputs come from files the user
supplies, some of which won't exist on a fresh install.

## Decision

1. **`load_profile()` returns a `Profile` with one `problems` line per missing input** (profile.md,
   preferences.yaml, rubric.yaml, resume, transcript). Nothing missing stops the run.
2. **A file that exists but is invalid still raises `ProfileFileError`.** Skipping a broken
   preferences file could let jobs through that break a hard constraint.
3. **Rubric** is `profile/rubric.yaml`: integer `version`, and criteria with `name`, `weight`
   (> 0), and `description`; names unique. `starter_rubric()` offers the PRD's five example
   criteria as a first draft; it is never written to disk automatically.
4. **Resume:** one `.tex` (or the one named `main.tex`/`resume.tex`) plus `.cls`/`.sty` files.
   **Transcript:** one PDF. Only presence is checked for the transcript; reading its text needs a
   PDF library and waits until a step needs it.
5. **`hub profile show`** (`careerhub.cli`, script `hub`) prints a summary, never the resume or
   transcript text. It opens the database only to count imported connections and still works
   (count "unknown") if the Keychain or database isn't available.

## Evidence

`tests/profile/test_assembly.py`: rubric validation, resume and transcript discovery and problems,
an empty and a complete data folder, and the command's output and exit codes.

## Alternatives considered

- **Fail when anything is missing.** Makes a first run unusable and hides what is present.
- **Write the starter rubric on first run.** Surprising file writes into the user's data folder.

## Consequences

- Step 6 (scoring) reads the rubric from here; step 9's Conversation panel proposes diffs to it.
- Transcript text extraction is a later ticket if scoring needs it.
