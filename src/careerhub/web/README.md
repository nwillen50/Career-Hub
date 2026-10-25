# `careerhub.web`

Pages Career Hub shows the user: the static nightly shortlist page, and the local web app
(`hub web`, on 127.0.0.1 only, FastAPI with server-rendered pages and HTMX bundled as a file;
ADR-0043).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `shortlist` | The nightly shortlist page: top 5 jobs with explanations | CH-69 | Built |
| `app` | The web app: routes, session link, Origin and CSRF checks, the tabs | CH-138 | Built |
| `today` | Today: top jobs, follow-ups due, deadlines this week, last run | CH-139 | Built |
| `job_detail` | One job: summary, score breakdown, people you know there | CH-141 | Built |
| `tracker_view` | Tracker: applications, saved views, inline edits, CSV export | CH-142, CH-157 | Built |
| `people_view` | Contacts and Interactions tabs | CH-171 | Built |
| `plan_view` | Plan: the weekly plan's suggested blocks | CH-143 | Built |
| `status_view` | System status: runs, sources, spending, models, readiness, what waits on you | CH-144 | Built |
| `browse_view` | Browse tab: every open job, search, filters (score, company, place, mode, age, source, reactions, removed-by-filters), sort, paging, group by place ; `/browse/label` (`modules/jobs/labeling.py`) labels postings one at a time into `evals/ranking.yaml` | CH-187, CH-200, CH-201 | Built |
| `profile_view` | Profile tab: what Career Hub knows about you, readiness, rubric, and facts waiting for Accept or Reject | CH-195 | Built |
| `grouping` | Group by place (country, then city) on Today and the Tracker | CH-121, CH-170 | Built |
| Conversation panel | Questions, and proposals for preferences, rubric, and sources (Accept or Reject) | CH-158 to CH-161 | Built |
| Browse | Search and filter every open job | CH-187 | Planned |

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

## Demo mode

`create_app(..., banner=text)` shows `text` in a strip above the top bar on every page. `hub demo`
(see `careerhub/demo.py`) uses it to label invented data "Demo data". Nothing else in the web app
knows about the demo: it reads the demo folder the same way it reads the real one.

Job cards on Today and Browse have a "Follow <company>" button (CH-198): it posts to `/jobs/<id>/track`, the same as the Job detail button, and makes network calls only when pressed.
