# 0034. Adding a job by pasting its link

- Status: Accepted
- Date: 2026-10-22
- Ticket: CH-96, for epic CH-21 (step 4)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Jobs often come from LinkedIn or Handshake. Their terms forbid scraping and the PRD never uses
them as a source, but the user can still find a posting there and want it ranked.

## Decision

`hub add-job <url>` tries, in order:

1. **A Greenhouse, Lever, or Ashby posting address**: fetched through that system's public API
   (Ashby has no per-job address, so its board listing is searched for the id).
2. **Any other address**: never requested. Company and title come from `--company`/`--title`, from
   the link's own text (`...-at-acme-1234567`), or a prompt. If the job is already stored, or an
   approved source for that company's own board lists it, that copy is used.
3. **Otherwise** the user's pasted description (`--text-file` or stdin) is stored with the original
   link.

A hard list (`NEVER_REQUEST`: linkedin.com, joinhandshake.com) is checked before any request, and a
test fails if a request to them is ever made. Manual jobs belong to the source `manual` and are never
closed by a listing (`store_jobs(close_missing=False)`); they go through the same extraction,
filters, scoring, and dedupe as any job. Adding a job is the user's own act, so the `manual` source
is created approved. Nothing is submitted anywhere.

## Evidence

`tests/modules/jobs/test_manual.py`.

## Consequences

A job added by hand is not refreshed or closed by Career Hub; the user closes it, or a company feed
that lists the same job takes over through dedupe.
