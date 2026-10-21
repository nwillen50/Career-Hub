# 0025. Fixes from the independent review

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-78 (bug)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

A separate reviewer read PRs #14 to #35 without seeing my reasoning and reproduced several
defects. Each was verified with a failing test before it was fixed.

## Decisions

1. **Only valid answers are cached.** `Router.complete` takes an optional `accept` check;
   `complete_validated` passes "parses as the schema". A rejected answer is not saved, and a
   rejected answer already in the cache is ignored and overwritten. (A truncated reply used to be
   replayed forever.)
2. **A duplicate stays visible when its original closes.** `only_visible_copies` hides a linked copy
   only while its original is open; the nightly run and `ranked_jobs` both use it.
3. **Scores and extraction notice changes.** `Assessment.input_hash` covers the profile text,
   posting, and extracted facts; extraction records a hash of the posting; tailoring's hash adds the
   posting too. Changed input means redo; unchanged means skip (new migration).
4. **An empty Greenhouse listing for a board that had open jobs is a failed fetch**, not "everything
   closed". A board that really closed everything stays `failing` until the user disables the source.
5. **A cloud provider without prices is reported** in `problems` (its spending isn't counted toward
   the budget). The essential-call ceiling of 2x is unchanged and intended (ADR-0020).
6. **Alumni tags match whole `;`-separated notes**, not substrings.
7. **Files with personal data are created private** (`write_private`, mode 600 from creation) instead
   of written then `chmod`ed; Greenhouse board names must match `[A-Za-z0-9_-]+`.

## Evidence

`tests/test_review_fixes.py` (one or more tests per item; all failed before their fix).

## Consequences

- Re-scoring happens whenever the profile changes, which costs model calls; the response cache and
  the budget bound it.
- Items 1 and 3 change behavior in PRs earlier in the stack; this PR stacks on top of them.
