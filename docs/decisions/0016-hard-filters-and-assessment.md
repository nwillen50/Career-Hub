# 0016. Hard filters and the Assessment table

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-66, part of CH-26 (epic CH-25, step 6)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

Step 6 requires that "filters never drop a 'great' posting" and that scores store their
explanation, model, and prompt version (CH-26). Hard constraints come from `preferences.yaml`.

## Decision

1. **`assessment` table**, one row per job (unique `job_id`), created now with the filter result
   and the score columns that CH-67 fills (`fit_score`, `explanation`, `model_used`,
   `prompt_version`, `rubric_version`). Odds and urgency (in the PRD's entity) are added when
   something computes them.
2. **Filters are pure code** (no model): excluded companies, excluded title words (whole words),
   locations by city or remote, minimum salary against the top of the listed range.
3. **Reject only on facts we have.** A missing location or salary is recorded under `uncertain`
   and the job passes. Every rejection stores a plain-English reason in `filter_results`.
4. **Extracted locations take priority** over the listed one; remote jobs pass the location rule
   only when `remote_ok` is true.
5. Reruns overwrite the job's row, so changing preferences re-filters cleanly.

## Evidence

`tests/modules/jobs/test_filters.py`: each rule, whole-word matching, remote handling, unknown
facts pass (the "never drop a good job" rule), and one saved row per job.

## Alternatives considered

- **Treat unknown as fail.** Drops good jobs whenever extraction is incomplete.
- **Delete filtered jobs.** The user couldn't see or dispute them; rows are kept with reasons.

## Consequences

- Ranking (CH-67) scores only jobs where `filter_passed` is true and the job is open and not a
  duplicate; the ranking eval (CH-68) checks that no labeled "great" posting is filtered out.
- City matching is simple (substring of the city name); it can be refined with the evals.
