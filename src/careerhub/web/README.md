# `careerhub.web`

Pages Career Hub writes for the user to read.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `shortlist` | The nightly shortlist page: top 5 jobs with explanations | CH-69 | Built |
| Today, Tracker, Job detail | The local web app | step 9 | Planned |

`hub shortlist build` writes `shortlist/index.html` in the data folder; bookmark it. The page is
static (no scripts, no external requests), escapes everything from postings, and links only to
http(s) addresses. See [ADR-0019](../../../docs/decisions/0019-shortlist-page.md).
