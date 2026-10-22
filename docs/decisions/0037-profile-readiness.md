# 0037. Scoring starts when the profile is ready

- Status: Accepted (thresholds flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-102 (part 1), for epic CH-25 (step 6)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

While the profile is being built, scores would change every night and cost money for nothing.

## Decision

1. A **readiness checklist** (`profile/readiness.py`): education with an expected graduation date;
   at least `profile_min_skills` skills (setting, default 5; accepted resume facts count, pending ones
   do not); interests or an About section; a resume that can be read; locations in
   `preferences.yaml` (work modes default to allowed); a rubric.
2. **`hub profile ready`** runs the checklist and, only if it passes, switches scoring on by writing
   `profile/ready.json` with the date. `hub profile ready --off` removes it. `hub profile show`
   prints the state and what is missing. A missing or damaged file means off.
3. While off, **`hub nightly` still ingests, reads, understands, and filters** (free or cheap), but
   does not score or write tailoring notes. The nightly summary says why, and the shortlist page shows
   "Scoring starts when your profile is ready" with the missing items.
4. Turning the switch on is the user's act; the checklist is never re-applied once it is on.

Not in this change: re-scoring only on meaningful profile edits (CH-115).

## Evidence

`tests/profile/test_readiness.py` and the nightly test in `tests/test_pipeline.py`.

## Consequences

Existing installs have scoring off until the user runs `hub profile ready` once.
