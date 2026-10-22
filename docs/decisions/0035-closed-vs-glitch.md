# 0035. Telling "all jobs closed" from a glitch

- Status: Accepted (backstop setting flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-103, for epic CH-21 (step 4)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

CH-78 treats an empty listing from a board that had open jobs as a glitch: jobs stay open and the
source is `failing` every night. If the company really closed everything, dead postings stay on the
shortlist until the source is disabled by hand.

## Decision

When a complete-listing board that had open jobs lists none:

1. **Spot-check** up to 3 open jobs through the system's per-job address where one exists
   (Greenhouse `boards/<board>/jobs/<id>`, Lever `postings/<company>/<id>`). If every checked job
   answers 404, the closures are real: every job from that board closes, the source is `ok`, and the
   nightly summary gets a note ("Acme: all 7 jobs closed").
2. If any answer is "still there", or a check fails (error, timeout, another status), it is a glitch,
   as before.
3. **Backstop**: after `empty_listing_backstop_nights` (setting, default 3) empty nights in a row the
   jobs close anyway, with a summary note. Ashby and The Muse have no per-job address and use only
   this (The Muse is partial and never reaches it).
4. Any non-empty listing resets the count; a board that lists jobs again reopens its jobs.

The count is kept as `empty_streak` in the source's `config` (text) to avoid a schema change for one
integer; it is removed when it resets.

## Evidence

`tests/modules/jobs/test_closed_vs_glitch.py`.

## Consequences

Up to three extra requests to a public API on the rare night a board is empty. A real closure on a
system with no per-job address takes three nights to show.
