# `careerhub.modules.jobs`

Discover postings and keep one row per job (build step 4, epic CH-21).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `store` | `NormalizedJob`, `store_jobs`: upsert, close missing, link duplicates | CH-59 | Built |
| `sources` | Propose, approve, reject, disable sources; `require_approved` | CH-61 | Built |
| `extract` | `JobFields` and `extract_and_save`: posting text into structured fields | CH-62 | Built |
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
