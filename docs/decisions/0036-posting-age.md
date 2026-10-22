# 0036. Posting age: a footnote and a gentle down-rank

- Status: Accepted (defaults flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-81, for epic CH-25 (step 6)
- Deciders: Sam (owner), Leo (reviewer for the page), Nicholas (approver)

## Context

A posting open for about three weeks is less likely to get a response but isn't dead. Nicholas
wants its age visible but small, and a slight ranking nudge, never a hidden job.

## Decision

1. **Age** comes from the source's posting date when its saved record has one, else the date Career
   Hub first saw the job (`modules/jobs/age.py`). Fields tried in order: `first_published`
   (Greenhouse, present on some boards), `publishedAt` (Ashby), `publication_date` (The Muse),
   `createdAt` (Lever, milliseconds), `updated_at` (Greenhouse, the last edit, so it can make a job
   look younger than it is). Unreadable and future dates are ignored.
2. **Settings** `stale_after_days` (default 21) and `stale_penalty_points` (default 5 of 100; 0
   turns it off). A job strictly older than the threshold loses those points in `ranked_jobs`'
   *ordering only*; the stored `fit_score` is never changed, and nothing is hidden.
3. **The page** shows one short muted line at the bottom of each card ("Posted 24 days ago", past
   the threshold "Posted 24 days ago, older than 3 weeks"): no badge, no colour.
4. `ranked_jobs` takes the adjustments as arguments; `current_rank_adjustments()` reads them from
   preferences and settings for the page, the weekly plan, and the tailoring selection.

## Evidence

`tests/modules/jobs/test_age.py`.

## Consequences

For Greenhouse boards without `first_published`, "age" is time since the last edit. If that proves
misleading, first-seen is the fallback to prefer for Greenhouse.
