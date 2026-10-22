# `careerhub.modules.jobs`

Discover postings and keep one row per job (build step 4, epic CH-21).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `store` | `NormalizedJob`, `store_jobs`: upsert, close missing, link duplicates | CH-59 | Built |
| `sources` | Propose, approve, reject, disable sources; `require_approved` | CH-61 | Built |
| `extract` | `JobFields` and `extract_and_save`: posting text into structured fields | CH-62 | Built |
| `rank_evals` | `run_ranking_eval`: labeled postings through the whole pipeline | CH-68 | Built |
| `tailoring` | `tailor_job`: what to lead with, gaps, resume edits (notes only) | CH-75 | Built |
| `scoring` | `score_job`, `ranked_jobs`: rubric scores with explanations | CH-67 | Built |
| `filters` | Hard filters (companies, title words, locations, salary) with reasons | CH-66 | Built |
| `evals` | `run_extraction_eval`: per-field accuracy against hand-checked postings | CH-63 | Built |
| `greenhouse` | Fetch a company's public board; `hub ingest greenhouse` | CH-60 | Built |
| `lever` | Same for Lever (`api.lever.co`); `hub ingest lever` | CH-93 | Built |
| `muse` | The Muse search (partial listing; shortlist says "via The Muse"); `themuse` sources | CH-95 | Built |
| `ingest`, `adapters` | The shared fetch-and-store routine and the adapter registry | CH-93 | Built |

An adapter turns its feed into `NormalizedJob`s and calls `store_jobs(session, source, jobs)` with
the source's **complete** listing. A rerun adds no duplicates; postings that disappear are marked
`closed`, not deleted; the same job at two sources is linked with `duplicate_of_id`.
See [ADR-0011](../../../../docs/decisions/0011-job-table-and-dedupe.md).

## Sources and approval

Sources start `proposed`. Only `approve_source` (via `hub sources approve <id>`) makes one usable,
and fetching code must call `require_approved` first. Commands: `hub sources list`,
`hub sources add <kind> <name> <url>`, `hub sources approve|reject|disable <id>`.
See [ADR-0012](../../../../docs/decisions/0012-source-approval.md).

## Lever

`hub sources add lever Acme https://jobs.lever.co/acme`, then `hub sources approve <id>`, then
`hub ingest lever --company acme`. Companies on Lever's European site add `"region": "eu"` to the
source's config. Same rules as Greenhouse: a failed fetch changes nothing, and an empty listing for
a board that had jobs is treated as a glitch. The nightly run fetches every approved source whose
kind is in `adapters.ADAPTERS`. The response shape was built from Lever's public documentation
(not checked against a live board from the build environment; see the PR).

## The Muse

`hub sources add themuse "The Muse" https://www.themuse.com --level "Entry Level" --category
"Software Engineering" --pages 3` (also `--location`), then approve it. It is a **partial** source: jobs missing from tonight's search stay open and
close only after 14 days unseen. Every job keeps its Muse address and the page credits "via The
Muse". The nightly run searches it with the other sources. See ADR-0010 and ADR-0011's amendment.

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

## Ranking evals

`run_ranking_eval(router, load_labeled(path), doc, rubric, constraints, extract_routes,
score_routes)` runs great/okay/no labeled postings through extraction, filters, and scoring in a
scratch in-memory database and reports whether the PRD rule holds (no great filtered out, at most
one "no" in the top 10). See [ADR-0018](../../../../docs/decisions/0018-ranking-eval.md) and
[`evals/README.md`](../../../../evals/README.md).

## Tailoring notes

For each shortlisted job (nightly, if a `tailor` route and `inputs/resume` exist) a model writes
notes: what to lead with, gaps to address, resume edits to consider. Notes only; nothing is edited or
sent. The resume goes to the model as plain text with emails, links, and phone numbers removed, and
only to providers allowed to see Personal data. See
[ADR-0024](../../../../docs/decisions/0024-tailoring-notes.md).
