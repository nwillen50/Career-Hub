# 0018. Ranking eval: the pipeline end to end, in a scratch database

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-68, part of CH-26 (epic CH-25, step 6)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

The PRD's acceptance rule for step 6: on the evaluation set, no posting labeled "great" is filtered
out, and the top 10 contains at most one "no". Reactions and labels come from the user (CH-7).

## Decision

1. **Labeled postings run through the real code**: extraction, hard filters, scoring (with the
   user's profile, rubric, and constraints), then are measured. One definition of "ranking".
2. **In a scratch in-memory SQLite database** created from the models, never the user's database,
   so an eval run changes nothing real and needs no Keychain.
3. **Measures:** great postings filtered out (must be none); "no" postings in the top 10 (at most
   one); the share of label pairs (great>okay, great>no, okay>no) in the right order; mean score per
   label; postings that couldn't be scored are listed, not hidden. `RankingReport.passed` is the PRD
   rule. Filtered-out and unscored postings rank last.
4. **File format** in `evals/ranking/` (sample provided): the extraction eval's posting fields plus
   `label` and a one-line `reason`.
5. **Unquoted YAML words:** YAML turns a bare `no` or `yes` into a boolean. Both eval loaders read
   those back as the words, so `label: no` and `visa_sponsorship: no` do what the author meant.

## Evidence

`tests/modules/jobs/test_rank_evals.py`: a good ranking passes with perfect pair order; a filtered
great posting fails; too many "no" in the top fails; wrong order lowers pair accuracy; unscorable
postings are listed; bad files and bare `no` handled.

## Alternatives considered

- **Evaluate scoring on its own (skip filters and extraction).** Would miss the interactions the
  acceptance rule is about.
- **Use the user's database.** Risks writing eval rows into real data.

## Consequences

- Each run makes real model calls (cost) when run for real; the response cache keeps reruns cheap.
- `hub eval` (one command for all evals, with comparison to the last run and cost) is a later
  ticket once there is real data to run it on.
