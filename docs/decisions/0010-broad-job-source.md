# 0010. The broad job source: Adzuna (proposed)

- Status: Proposed
- Date: 2026-10-21
- Ticket: CH-10 (spike), for epic CH-21 (step 4)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

The PRD wants "one broad source" next to the per-company Greenhouse, Lever, and Ashby feeds, and
asks that it be chosen after checking which offer terms-compliant access (PRD, open questions).
Criteria: allowed for a personal tool, a free key or none, US early-career coverage, stable API.

## What I could and couldn't check

This cloud session could search the web but **could not open the providers' own documentation**
(the fetch failed to resolve their hosts). Everything below comes from search snippets, several
from third parties, and **has not been verified against official terms**.

| Candidate | Access | Notes from snippets | Fit |
| --- | --- | --- | --- |
| Adzuna | Free self-serve key | Default limits about 25/min, 250/day, 2,500/month; terms list "personal research" and publishing listings as permitted uses; some versions say commercial users must agree terms in advance; attribution link required | Good: US coverage, search by keyword and location |
| The Muse | Free, key optional | Reported 500 requests/hour without a key; must link back; no scraping | Medium: curated, fewer postings, skewed to larger employers |
| USAJOBS | Free key | Not confirmed by this search | Narrow: federal jobs only |
| Arbeitnow | No key | Mostly Germany and remote | Poor for US |
| Hacker News "Who is hiring" | Public API | Free text, not structured; terms not confirmed | Later (PRD lists it as a later source) |

## Decision (proposed)

Use **Adzuna** as the MVP broad source, behind the same adapter interface as Greenhouse, so it can
be swapped. Build nothing against it until Nicholas has registered a key and read the current
terms (ticket CH-58, a task owned by Nicholas).

## Alternatives considered

The rest of the table. The Muse is the fallback if Adzuna's terms don't fit a personal tool.

## Consequences

- Respect the rate limits (a nightly run is far below them) and show attribution in the UI.
- Postings from an aggregator are normalized and deduplicated against company feeds (CH-22).
- If the current Adzuna terms forbid this use, switch to The Muse and amend this record.
