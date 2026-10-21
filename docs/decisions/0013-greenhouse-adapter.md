# 0013. Greenhouse adapter: public feed, fail without changing anything

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-60, part of CH-22 (epic CH-21, step 4)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

Step 4's first source is a company's public Greenhouse board (PRD: public data only, no key).

## Decision

1. **Endpoint:** `GET boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true`. The board
   name is `config["board"]` on the source (set from the URL by `hub sources add`).
2. **Normalization:** id, title, URL, location, and the description as plain text (Greenhouse
   sends HTML-escaped HTML; converted with the standard library). The full record is kept in
   `raw_payload`. The company name comes from the record's `company_name`, else the source's name.
3. **A failed fetch changes nothing.** Network error, non-200, or an unexpected reply raises
   `FetchError` (status code only, never body text), marks the source's health `failing`, and
   does not call `store_jobs`, so open postings are never closed by a bad answer.
4. **Only approved sources are fetched** (`require_approved`, ADR-0012).
5. **`hub ingest greenhouse --company <board>`** finds the source by board name and prints a count
   summary. Tests use a mock transport and made-up JSON in Greenhouse's shape.

## Evidence

`tests/modules/jobs/test_greenhouse.py`: normalization, no request for an unapproved source,
rerun adds nothing, four failure modes leave jobs untouched, and the command.

## Alternatives considered

- **Scraping the public careers page.** Fragile and unnecessary; the JSON feed is public.
- **Closing postings on an empty list.** A bad or empty answer would wipe a board; a genuinely
  empty board is handled by a valid empty `jobs` list, which does close postings.

## Consequences

- Not run against the live API from the cloud (tests are offline); a Mac session should run
  `hub ingest greenhouse` against a real board once.
- Lever and Ashby adapters follow the same shape.
