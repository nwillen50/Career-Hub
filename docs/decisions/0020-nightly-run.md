# 0020. The nightly run, and a ceiling on essential spending

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-70, part of CH-27 (epic CH-25, step 6)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

Milestone A needs one command that does the night's work and leaves a readable page, even when
parts fail. PRD: a failed nightly run shows on the page; spending stays within the monthly budget.

## Decision

1. **`hub nightly` = `run_nightly`**: fetch every approved Greenhouse source; extract, filter, and
   score every open, non-duplicate job that needs it (reruns skip finished work); write the
   shortlist page; back up the database. Recorded as a `run_log` row (step `nightly`).
2. **Keep going.** A failed source, a job the model can't answer for, a missing key, or a missing
   route is counted in `problems` and skipped; the other work and the page still happen. Missing
   profile files or routes skip only the stages that need them.
3. **The run is `failed` only when nothing useful happened:** every approved source failed, or the
   page can't be written. That status is what the page shows as "The last nightly run failed".
4. **The page and backup are written after the run is recorded**, so the page shows this run's
   outcome and finish time, and the backup includes the run log. (A first draft wrote them
   before and showed stale status; a test now covers it.)
5. **Budget ceiling.** Extraction and scoring are `essential`, so a budget stop on non-essential
   calls alone would never limit them. Essential cloud calls now also stop, at **2x the monthly
   budget** (`ESSENTIAL_CEILING`), so spending has an upper limit that matches the PRD's "stays
   within the user's budget" more closely. When it is hit the run pauses model work, writes the
   page, and says so. This amends ADR-0006.
6. Problems are short lines with type names and counts: no posting, profile, or response text.

## Evidence

`tests/test_pipeline.py`: the whole pipeline against a mock Greenhouse board and a mock local model
server; rerun does nothing new; every source failing marks the run failed and the page says so;
one bad source doesn't stop another; model failures, missing models, missing profile, budget
ceiling; and the command. `tests/llm/test_cost.py` covers the ceiling.

## Alternatives considered

- **Fail the run on any problem.** One flaky company board would blank the page every morning.
- **Mark core calls non-essential so the budget limits them.** Would stop the shortlist at the
  budget instead of 2x; a ceiling keeps the core running a bit longer but still bounded. Nicholas
  can tighten the factor.

## Consequences

- Scheduling (CH-71) only has to run `hub nightly`.
- Lever and Ashby sources are not run yet (no adapters).
