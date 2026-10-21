# `careerhub.modules.jobs`

Discover postings and keep one row per job (build step 4, epic CH-21).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `store` | `NormalizedJob`, `store_jobs`: upsert, close missing, link duplicates | CH-59 | Built |
| `sources` | Propose, approve, reject, disable sources; `require_approved` | CH-61 | Built |
| Greenhouse adapter, `hub ingest` | Fetch a company's public board | CH-60 | Planned |

An adapter turns its feed into `NormalizedJob`s and calls `store_jobs(session, source, jobs)` with
the source's **complete** listing. A rerun adds no duplicates; postings that disappear are marked
`closed`, not deleted; the same job at two sources is linked with `duplicate_of_id`.
See [ADR-0011](../../../../docs/decisions/0011-job-table-and-dedupe.md).

## Sources and approval

Sources start `proposed`. Only `approve_source` (via `hub sources approve <id>`) makes one usable,
and fetching code must call `require_approved` first. Commands: `hub sources list`,
`hub sources add <kind> <name> <url>`, `hub sources approve|reject|disable <id>`.
See [ADR-0012](../../../../docs/decisions/0012-source-approval.md).
