# 0014. Job extraction: versioned prompt, strict schema, public tier

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-62, part of CH-24 (epic CH-23, step 5)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Step 5 turns posting text into structured fields (level, location, visa, deadline, ...) that the
filters and scorer use. The PRD says prompts are versioned files with tests, model output is
checked against a schema, and raw text is kept so extraction can be redone.

## Decision

1. **Prompt file** `src/careerhub/prompts/extract_job_v1.md` with `{{placeholders}}`, filled in one
   pass so posting text can never be mistaken for a marker. A prompt change is a new version file;
   results record `prompt_version`.
2. **`JobFields` schema**: level, locations, remote, visa sponsorship, deadline, minimum years,
   salary range, required skills. Unknown facts are `unknown`, null, or empty; "do not guess" is in
   the prompt. Extra keys from the model are ignored.
3. **Postings are untrusted text.** The prompt frames them as data, and output goes through
   `complete_validated`, so an injected posting can at worst skew these fields.
4. **Tier PUBLIC, `essential=True`** (the shortlist can't work without it). Any provider may be
   used, including cloud ones that train on data; postings are public.
5. **Saved in `Job.structured_fields`** (`fields`, `prompt_version`, `extracted_at`); a job already
   extracted with the current version is skipped, so reruns cost nothing. Postings are cut to 12,000
   characters first.
6. The accuracy evals are CH-63; connecting real providers to the nightly run is CH-64.

## Amendment (CH-98, Oct 22): prompt v2

`extract_job_v2.md` adds fields about eligibility and about what the job is: `degree_required`,
`degree_preferred`, `degree_fields`, `equivalent_experience_ok`, `graduation_earliest/latest`,
`work_authorization`, `start_date`, `preferred_skills` (kept apart from required skills; they never
lower a score), `summary` (2 to 3 sentences, only from the posting), and `role_focus`. All default
to "unknown", null, or empty, so results saved with v1 still load; the version bump makes every
posting extract again (Public tier, so a free Gemini key is fine). The answer budget grew from 700
to 1,200 tokens. The extraction eval can state expected values for `degree_required`,
`equivalent_experience_ok`, and `work_authorization`; those are scored only for cases that state them.
What the fields are used for (filters, scoring) is CH-113 and CH-114.

## Evidence

`tests/modules/jobs/test_extract.py`, `tests/test_prompts.py`: request shape, validation, retry and
escalation, no echo of bad answers, public tier, saving and skipping, defaults.

## Alternatives considered

- **Free-text extraction then regexes.** Brittle across employers.
- **One `visa` boolean.** Postings often say nothing; `unknown` must be distinct from `no`.

## Consequences

- Field accuracy is unmeasured until CH-63 and Nicholas's hand-checked postings.
- Which models do extraction ("cheap" and "strong" routes) is decided by the evals, per the PRD.
