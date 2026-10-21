# `careerhub.modules.jobs`

Discover postings and keep one row per job (build step 4, epic CH-21).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `store` | `NormalizedJob`, `store_jobs`: upsert, close missing, link duplicates | CH-59 | Built |
| `sources` | Propose, approve, reject, disable sources; `require_approved` | CH-61 | Built |
| `extract` | `JobFields` and `extract_and_save`: posting text into structured fields | CH-62 | Built |
| `scoring` | `score_job`, `ranked_jobs`: rubric scores with explanations | CH-67 | Built |
| `filters` | Hard filters (companies, title words, locations, salary) with reasons | CH-66 | Built |
| `evals` | `run_extraction_eval`: per-field accuracy against hand-checked postings | CH-63 | Built |
| `greenhouse` | Fetch a company's public board; `hub ingest greenhouse` | CH-60 | Built |

An adapter turns its feed into `NormalizedJob`s and calls `store_jobs(session, source, jobs)` with
the source's **complete** listing. A rerun adds no duplicates; postings that disappear are marked
`closed`, not deleted; the same job at two sources is linked with `duplicate_of_id`.
See [ADR-0011](../../../../docs/decisions/0011-job-table-and-dedupe.md).

## Sources and approval

Sources start `proposed`. Only `approve_source` (via `hub sources approve <id>`) makes one usable,
and fetching code must call `require_approved` first. Commands: `hub sources list`,
`hub sources add <kind> <name> <url>`, `hub sources approve|reject|disable <id>`.
See [ADR-0012](../../../../docs/decisions/0012-source-approval.md).

## Greenhouse

`hub sources add greenhouse Acme https://boards.greenhouse.io/acme`, then
`hub sources approve <id>`, then `hub ingest greenhouse --company acme`. A failed fetch changes
nothing (jobs are never closed by a bad answer) and marks the source `failing`.
See [ADR-0013](../../../../docs/decisions/0013-greenhouse-adapter.md).

## Extraction

`extract_and_save(session, router, job, routes=["cheap", "strong"])` asks a model (prompt
`prompts/extract_job_v1.md`) for level, locations, remote, visa sponsorship, deadline, and more,
validates the answer, and saves it in `job.structured_fields` with the prompt version. A job already
extracted with this version is skipped. See
[ADR-0014](../../../../docs/decisions/0014-job-extraction.md).

## Extraction evals

`run_extraction_eval(router, load_cases(path), routes)` measures accuracy for level, locations,
visa sponsorship, and deadline and lists every miss. File format and scoring rules:
[`evals/README.md`](../../../../evals/README.md). The real hand-checked set is CH-65 (Nicholas).

## Hard filters

`assess_filters(session, job, preferences.hard_constraints)` saves an `Assessment` with
`filter_passed` and `filter_results` (`reasons` for a rejection, `uncertain` for facts it couldn't
check). A job is rejected only when a known fact breaks a rule; missing information passes. See
[ADR-0016](../../../../docs/decisions/0016-hard-filters-and-assessment.md).

## Scoring

`score_job(session, router, job, profile_doc, rubric, routes)` has the model rate each rubric
criterion (0 to 10) and explain; the code computes the 0-100 fit score and saves it on the job's
`Assessment` with the model, prompt version, and rubric version. Only filtered-in jobs are scored.
`ranked_jobs(session)` lists open, original jobs best first. See
[ADR-0017](../../../../docs/decisions/0017-rubric-scoring.md).
