# `careerhub.web`

Pages Career Hub writes for the user to read.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `shortlist` | The nightly shortlist page: top 5 jobs with explanations | CH-69 | Built |
| Today, Tracker, Job detail | The local web app | step 9 | Planned |

`hub shortlist build` writes `shortlist/index.html` in the data folder; bookmark it. The page is
static (no scripts, no external requests), escapes everything from postings, and links only to
http(s) addresses. See [ADR-0019](../../../docs/decisions/0019-shortlist-page.md).

## What a card shows

Each job card shows a score bubble, a one-sentence reason (with the full explanation and criteria
table folded away), the people you know there, tailoring notes when there are any, and small muted
lines at the bottom:

- **Posting age** ("Posted 3 days ago", with "older than 3 weeks" past the `stale_after_days`
  setting; older postings rank slightly lower, never hidden), ADR-0036.
- **"via The Muse"** when the posting came from The Muse, which asks for credit.
- **"Same school"** on a contact who went to your school, and, when you set `linkedin_school_slug`,
  an **alumni search link** (built only; Career Hub never requests LinkedIn), ADR-0039.
- The internship ordering note when you set an internship penalty.

## Who to contact

Each job lists up to two people you know at the company (from `hub paths` rules, ADR-0023) with the
reasons, or says that you don't know anyone there yet. Names, titles, and reasons are escaped like
all page text. Your own `how_known` notes appear in the reasons, so the page stays in the private
data folder.
