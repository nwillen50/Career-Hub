# 0011. The Job table and deduplication

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-59, part of CH-22 (epic CH-21, step 4)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

Step 4 is done when one company's postings are in the database and a rerun adds no duplicates
(PRD). ADR-0002 says the step that first needs a table creates it.

## Decision

1. **`job` table** with the PRD's fields: company, source, title, URL, first and last seen, raw
   text, structured fields (empty until step 5), status (`new`, `stale`, `closed`). Also the
   source's `external_id`, `location`, the full `raw_payload`, a `dedupe_key`, and `duplicate_of_id`.
2. **Identity is (source, external id)**, enforced by a unique constraint. A rerun updates the row
   (title, URL, text, last seen) instead of adding one.
3. **Adapters produce `NormalizedJob` and hand a complete listing to `store_jobs`.** A posting the
   source no longer lists becomes `closed` (never deleted); one that returns is reopened. Adapters
   must only pass a *complete* listing, or open jobs would be closed by mistake.
4. **Cross-source duplicates** share a `dedupe_key`: SHA-256 of the company, title, and location
   with case, spacing, and punctuation removed. The later posting points to the earliest with
   `duplicate_of_id`; ranking will show originals only.
5. `stale` is defined in the schema but set by a later ticket (reposted or open unusually long).
6. The shared `company_key` helper moved to `careerhub.core.names`.

## Amendment (CH-95, Oct 22): partial listings

A search API (The Muse) never returns every job, so its source is **partial**: `store_jobs(...,
partial=True)` does not close a job for being missing from one night's results. A partial source's
job closes only when it has not been seen for 14 days (`PARTIAL_CLOSE_DAYS`), and reopens if it
returns. An empty search from a partial source is normal, not a "glitch" (the ingest rule that keeps
jobs open and marks the source failing applies to complete listings only). Whether the API reports a
job as gone is not used yet.

## Evidence

`tests/modules/jobs/test_store.py`: new, rerun, close, reopen, cross-source linking, different
titles not linked, and the database refusing a duplicate (source, external id).

## Alternatives considered

- **Merge duplicates into one row.** Loses which source listed it and its raw data.
- **Fuzzy title matching.** Risks hiding distinct roles; exact-after-normalization is predictable
  and can be loosened with evidence.

## Consequences

- Reposts under a new external id at the same source are not yet detected (later stale ticket).
- Location differences ("Boston" vs "Boston, MA") will miss duplicates; acceptable for now.
