# Career Hub PRD

> **This is the PRD every session works from.** It was approved by Priya, Sam, Leo, and Nicholas
> on Oct 19, 2026, and copied here from the PRD document on Oct 20, 2026. Changes go through a pull
> request that Nicholas approves. Where an accepted decision record in `docs/decisions/` refines
> this document, the decision record wins until the PRD is updated to match.
>
> **Updated Oct 21, 2026** with the decisions Nicholas made reviewing PRs #13 to #41 (CH-106,
> covering the approved PRD tickets CH-41, CH-82, CH-88, and CH-92). The main changes: Apple
> Calendar is the MVP calendar, with direct Google and Outlook sync moved to Post-MVP; job sources
> are Greenhouse, Lever, Ashby, The Muse, and pasted links; resumes and transcripts can be any
> common format; eligibility and job summaries are extracted; the interface gains a Plan tab; and a
> built-in set of models is switched on with the user's own keys.
>
> Nicholas's notes from review are kept, marked with his name (typos fixed). Open questions
> answered since sign-off are checked off, with the ticket that settled them.

## Executive summary

Career Hub is a local-first personal assistant for career decisions. Its first module, **Jobs**,
finds full-time roles, ranks them with explanations, maps warm paths through your network, and
plans your week around the time you actually have.

It runs on your Mac. Sensitive data stays local and encrypted, and every model call goes through
one router that enforces privacy tiers across cloud and local models. It drafts and advises but
never sends, submits, or applies on your behalf.

The MVP is a local web app. **Today** is a daily digest, **Plan** is the week's plan, and
**Tracker** is a sheets-like table of applications, contacts, and interactions. Job detail, a
Conversation panel, and System status complete it, and a menu-bar icon makes sure nothing gets missed. A ranked shortlist (Milestone A)
arrives first, before the interface is built. News, Grad Schools, and a version others can install
come later.

The code is public on GitHub and built bottom-up: small, tested, documented modules, each runnable
on its own before the next one is built on top.

## Problem and motivation

The people with the most career options (students) have little time to act on them. Work or
school takes most of the week; it's impossible to give enough time to the job search. Something
gives: either the day job suffers, or the search and the networking do.

The job search is intimidating, siloed across many boards, saturated with postings,
time-sensitive, and uninformative about what a role is really like. Networking is time-consuming,
often unproductive, and intimidating to start. News (a later module) is too frequent to keep up
with, scattered across sources, biased in perspective, disconnected from related stories, and
focused on the present without the history behind it.

The cost of all this is concrete: missed deadlines, generic applications, and a network that never
gets used.

Career Hub does the exhaustive reading and bookkeeping. It leaves the user the two things only they
can do: make the decisions and have the human conversations.

## Users, vision and scope

The first user is the builder: a college senior looking for full-time roles who wants an offer by
early spring. **Nothing about that user is hard-coded.** Profile, constraints, timeline, providers,
and schedule are all settings, so anyone can run it for themselves. Jobs can be in any country:
the user says where they can already work, and the app flags the rest.

**Later users** clone it from GitHub and run it on their own machines. That includes other college
students, grad students, and people early in their careers. High school students are a possible
audience for the Colleges module.

**Use pattern:** casual most days, intense when needed. It gets more useful as it learns more about
the user.

| Module | Purpose | Phase |
| --- | --- | --- |
| Jobs | Find, rank, and plan full-time applications | MVP |
| Connections | Who you know, interactions, follow-ups (shared by all modules) | MVP, as part of the core |
| News | Correlated, de-biased news on your interests and target companies | Post-MVP, second module |
| Grad Schools | Programs, labs, and deadlines - Akin to jobs but with grad school | Later |
| Colleges | Admissions, for high school users | Later |
| Explore | Suggests new topics and directions | Later |
| Hobbies | Non-professional interests on their own screen | Post-MVP |

## Product principles and non-goals

Seven principles decide every trade-off:

1. **Human in the loop.** It drafts and recommends. It never sends, submits, or applies. Every
   outbound action is taken by the user.
2. **Local-first.** Data lives on the user's machine, encrypted. A cloud model receives only what
   the privacy tier of that data allows.
3. **Explain everything.** Every score, ranking, and suggestion stores its reasoning, so the user
   can audit it. *Nicholas: Yes and users can develop the metrics used through conversations with
   agent.*
4. **Structure first, agent second.** A fixed pipeline does the work. Models fill specific steps
   inside it. The agent works within the structure; it doesn't invent new structure each run.
5. **Bottom-up.** Every module runs and passes its tests on its own before anything is built on top
   of it. *Nicholas: Super important. We don't have a huge engineering team building this, so we
   need to become one. As we build, we document, engineer, test, review, reevaluate frameworks,
   etc.*
6. **Configurable, not hard-coded.** The user, providers, models, and schedules are all settings.
7. **Light on the machine.** Heavy work runs overnight. Nothing heavy runs while the user is
   working.

**Non-goals for the MVP:**

- Applying automatically or sending messages on the user's behalf
- Scraping LinkedIn, or any site whose terms forbid it
- Hosting for multiple users, accounts, and sign-in
- A mobile app
- Replacing the user's judgment: it ranks and explains, and the user decides

## Success metrics

Career Hub succeeds if the user spends less time on search admin and more on the applications and
conversations that lead to an offer. These targets are starting points. Priya reviews them with
Nicholas every other week, together with the eval set.

| Metric | Starting target | How it's measured |
| --- | --- | --- |
| Shortlist precision | At least half of each day's top 5 marked "interested" | Reactions on Today |
| Time on Today | 10 minutes a day or less | Time on page (local only), plus a weekly self-check |
| Missed follow-ups | None overdue by more than 3 days | Tracker |
| Weekly plan | Planned applications and outreach completed | Plan vs. actual |
| Warm paths used | Share of applications with a referral or a contact reached | Tracker |
| Cost | Within the monthly budget | Run log |

The lagging outcome is interviews per application, and an offer by the user's target date.

## Jobs module capabilities

The Jobs module covers the whole path from finding a posting to following up after applying. The
MVP includes a basic version of most capabilities, and later phases deepen them.

| Capability | What it does | MVP | Post-MVP |
| --- | --- | --- | --- |
| Discover | Pulls postings from approved sources, removes duplicates, flags stale and ghost postings (reposted, or open unusually long) | Greenhouse, Lever, and Ashby feeds for a target company list (seeded from lists the user adds, such as The Engine's resident companies); The Muse as the broad source; jobs the user adds by pasting a link from anywhere, including LinkedIn and Handshake. Posting age is shown, and postings older than about three weeks rank slightly lower | More applicant-tracking systems (Workday, Rippling, Workable); job-alert emails from LinkedIn and Handshake once mail is connected; a second broad source (Adzuna) if needed |
| Explore | Adds one exploration pick a day by default (up to two): an adjacent role or company outside stated preferences. Reactions update an interest model, and the agent states what it has inferred so the user can confirm or correct it | One pick a day by default; inferred interests shown for confirmation | Number of picks and the interest model learn from feedback |
| Source discovery | Proposes new sources based on the user's interests, conversations, and reactions. In the MVP it can only propose new configurations of existing source types (another company's job board, a new saved search); a new type of source is a code ticket. Nothing is added without the user's approval | Yes | — |
| Understand | Turns each posting into structured fields (level, requirements, location as city, region, and country, visa sponsorship, whether the posting requires an existing right to work in its country, pay, deadline, degree required and preferred, degree fields, graduation window, work authorization, start date, required and preferred skills), a plain-English summary of what the job really is, and company context | Yes | Deeper company research |
| Prioritize | Hard filters, then a fit score with a written explanation, then ranking by fit, odds, and urgency. Hard filters are only eligibility (a required degree level above the user's with no "or equivalent experience", a graduation window that excludes the user) and the user's explicit constraints. A job in a country where the user can't already work gets a work-visa flag stating the employer's sponsorship stance; it is hidden only if the user turns that on and the posting says it won't sponsor. Scoring uses every job field plus the user's preferences, judges degree fields and skills generously (physics counts toward most engineering; recent grad equals new grad), and ignores preferred skills. Scoring starts once the profile is ready, and small profile edits don't trigger a re-score | Yes | Scoring tuned by feedback |
| Network | Who you know at each company, and the strongest warm path | From the LinkedIn connections export, plus alumni from lists the user provides (any layout) and connections the user tags. Anyone known to share the user's school is highlighted, and each company links to LinkedIn's alumni search for the user to open | Second-degree connections, past email contact, contacts no longer in the LinkedIn export flagged, your own contacts (vCard export, or Apple Contacts opt-in) |
| Tailor | Which experiences to lead with, which gaps to address, suggested resume changes, and the posting's preferred skills to highlight | Written tailoring notes | Generates resume variants in LaTeX (for LaTeX resumes) for the user to accept |
| Outreach | Drafts messages to contacts, with etiquette limits on how often to contact a person or company | — | Yes |
| Track | Applications, contacts, interactions, and follow-up dates; the agent checks every run that nothing slips | Yes | Status detected from Gmail |
| Plan | Builds a weekly plan from the time budget, deadlines, recruiting timelines, and free time | Yes, with every default (task lengths, working hours per weekday, daily share) adjustable in preferences | Puts suggested blocks on the calendar after the user approves |
| Interview prep | Prep notes for a specific interview | — | Yes |
| Relevant news | News about target companies and fields | — | Part of the News module |
| Conversation | Talk with Career Hub to refine interests, preferences, and the scoring rubric, or ask why a job ranks where it does. Every change arrives as a proposal the user accepts or rejects | Scoped panel: preferences, rubric, ranking explanations | Full Ask: prep research and actions |

## User experience

The user never needs a terminal. Everything happens in a local web app opened in the browser, and
a menu-bar icon means nothing gets missed.

### Screens

- **Today**, the daily digest, about 10 minutes. It shows at most:
  - 5 new top jobs plus 1 exploration pick (up to 2). Each card's headline is the title, company,
    location, salary, and overall score (if it doesn't fit, salary is the part dropped). An
    expandable section, closed by default, holds the reason, the rubric breakdown, a short summary
    of the job, and who to contact
  - up to 5 follow-ups due, plus a "+N more overdue" link to Tracker
  - deadlines this week
  - when the last successful run finished

  The user can show all sections or one at a time (Top jobs, Exploration, Follow-ups, Deadlines).
- **Plan.** This week's plan and progress against it, on its own tab.
- **Tracker.** A sheets-like table of applications, contacts, and interactions. Sort, filter by
  status and text, edit inline, pick a status from a dropdown (interested, reached out or referred,
  applied, interviewing, offer, rejected, withdrawn, ghosted), and set follow-up dates. Saved views:
  Follow-ups due; Applied, no reply in 10+ days; Interviewing; Deadlines in the next 7 days;
  Interested but not applied for over a week; Has a warm path; Interested. Plus CSV export.
- **Job detail.** One job in full: a short summary of what the job is, the fit explanation, warm
  paths, tailoring notes, the posting (collapsed by default), and feedback buttons.
- **Conversation panel.** A side panel on Today and Job detail. Use it to refine interests,
  preferences, and the scoring rubric, or to ask why a job ranks where it does. Changes appear as
  proposals with Accept and Reject. Declined proposals stay in a list for 30 days and aren't
  proposed again for 90.
- **System status.** Last run, errors, source health, and this month's model spending.
- **Profile and settings.** The MVP edits plain files. Post-MVP, this becomes a form.
- **Ask** (Post-MVP). A plain-English chat pane for requests like "prep me for Acme" or "mark Acme
  as applied." It finds prep material online, internalizes it, and uses it to help the user
  prepare.

### Cadence

- **Daily, about 10 minutes:** read Today, react to jobs, act on one or two follow-ups.
- **Weekly, about 30 minutes:** review the pipeline, set next week's time budget, confirm targets
  and inferred interests.
- **On demand:** a deep dive on a job before applying.

### Getting the user's attention

The goal is to be impossible to miss without flooding the inbox:

1. **Menu-bar icon** with a count of pending actions: follow-ups due plus new top jobs not yet
   reacted to. It stays visible, unlike a notification banner that disappears.
2. **One macOS notification** each morning when the digest is ready. Clicking it opens Today.
3. **Email to self, as an escalation only.** Sent if Today hasn't been opened in two days, plus an
   optional weekly summary. Off by default. The code restricts sending to the user's own address.

### Feedback

Every job has quick reactions: interested, not interested (with a one-click reason: level,
location, field, company, or pay), and applied. These reactions feed scoring and the interest
model.

### States and accessibility

- Every screen has designed empty, loading, and error states.
- If the nightly run fails, the menu-bar icon shows an error state and Today says what failed.
- Keyboard shortcuts cover the daily loop: j and k to move, i for interested, x for not
  interested, a for applied. Interested is one click or key from Today, Job detail, and Tracker.
- Meets WCAG AA contrast, works with VoiceOver, and follows the system's light or dark mode.
- The visual design (look, type, color, spacing, navigation feel) is decided with Nicholas before
  the interface is styled (CH-105); the wireframes are in `docs/design/wireframes.md`.

### The terminal

The terminal is a developer tool, not the user's interface. Each module has a command so it can be
built and tested on its own, for example `hub ingest greenhouse --company acme` or
`hub score --dry-run`. Daily use never requires it.

## System architecture

Career Hub is a set of Python packages stacked in layers on one shared core. Each layer uses only
the layers below it, so every piece can be tested alone. News and Grad Schools will reuse the same
core and shared services.

```
Interface        Today · Plan · Tracker · Job detail · Menu-bar icon
Modules          Jobs (MVP): Discover · Understand · Prioritize · Network · Advise · Plan
                 Later: News · Grad Schools · Colleges · Explore
Shared services  Profile (resume, transcript, prefs) · Tracker store (apps, contacts)
                 Model router (privacy tiers) · Scheduler (launchd jobs)
Providers        Job sources (ATS boards, The Muse, pasted links)
                 Calendar (Apple; Google and Outlook later) · Mail (Google, Outlook; later)
                 Network (LinkedIn export, alumni lists) · LLM providers (cloud and local)
Core             Data models · encrypted SQLite (SQLCipher) · Keychain secrets · config · logging
```

The interface reads from the modules, and the modules call shared services. Only providers touch
the outside world.

### Runtime on the Mac

| Piece | What it is | When it runs |
| --- | --- | --- |
| Pipeline | Ingest, understand, prioritize, network, advise, plan; started by launchd as low-priority background work | Full run overnight (only when plugged in); a light refresh midday |
| Web app | FastAPI with server-rendered pages and HTMX; listens on the local machine only | Starts at login, sits idle until opened |
| Menu-bar helper | Small Python app showing the pending-action count | Always on, minimal footprint |
| Local models | Open-weight models through MLX or Ollama | Overnight batches only; unloaded afterward |
| Data | Encrypted SQLite (SQLCipher), resume source, profile files | In ~/Library/Application Support/CareerHub, never in the repo |

### Efficiency requirements

- Pipeline jobs start, do their work, and exit. No heavy process stays resident.
- launchd runs the pipeline as background work with low-priority disk access.
- Network and model calls run with capped concurrency.
- Local models load only during the overnight batch. Daytime work uses cloud calls or no model at
  all.
- Targets for idle memory of the web app and menu-bar helper are set once they can be measured,
  during build step 9.

### Pipeline rules

- Each stage (ingest, understand, prioritize, network, advise, plan) reads from and writes to the
  database only, keyed by run. Any stage can rerun alone, and reruns are safe.
- Prompts are versioned files in `prompts/`, each with tests.
- The scoring rubric is a versioned config file (see Model routing).
- The encrypted database is backed up nightly to a local folder. Time Machine covers the rest.
- SQLCipher with SQLAlchemy and Alembic gets a spike at the start of step 1. Fallback: plain SQLite
  inside an encrypted APFS volume.

## Data sources and integrations

Each kind of data has one interface with interchangeable providers. The user picks providers in
settings, and nothing above the provider layer knows which one is in use. Access is always
read-only and as narrow as possible.

| Interface | MVP providers | Later providers | Access needed |
| --- | --- | --- | --- |
| Job source | Public job-board feeds from Greenhouse, Lever, and Ashby for a target company list; The Muse as the broad source; jobs added by pasting a link; approved proposals of existing source types | Other applicant-tracking systems (Workday, Rippling, Workable), aggregators, "who's hiring" threads, job-alert emails, new types of sources | Public data only. LinkedIn and Handshake are never fetched or scraped |
| Calendar | Apple Calendar (EventKit, on the Mac), which includes Google and Outlook calendars added to the Mac's Calendar app; exported calendar files | Direct Google Calendar and Outlook (Microsoft Graph) sync, free/busy only | Free/busy times only; event titles are not needed. Apple Calendar has no free/busy-only access, so Career Hub reads only start, end, and availability and never stores titles or other event details |
| Mail | — | Gmail, then Outlook | Read-only, to detect application status and recruiter replies |
| Network | LinkedIn data export (connections CSV), re-imported monthly; alumni lists in any common layout (CSV or Excel) | Alumni directories, contacts found in mail, your own contacts (a vCard export from any phone or address book, or Apple Contacts, opt-in on the Mac) | A file the user downloads |
| Models | A built-in set (Anthropic, OpenAI, Google, and local MLX or Ollama) that the user switches on with their own API keys; others can be added in settings | Others as needed | API keys kept in the Keychain |

**LinkedIn constraint:** LinkedIn offers no personal API for this, and scraping it breaks its terms
and puts the account at risk. The official data export is the supported path. This gap is the main
data-access risk (see Risks).

**Alumni:** LinkedIn's connections export lists company and title, but not schools. MVP alumni come
from lists the user provides (a CSV, such as a club or career-office export where its terms allow
it) and from connections the user tags as alumni. Scraping school directories is excluded.

**LinkedIn and Handshake jobs:** neither offers a public jobs API to individuals, and scraping
breaks their terms. Many of their postings come from Greenhouse, Lever, or Ashby, which the company
feeds already cover. For any other job, the user pastes the link: Career Hub looks for the same
posting on the company's own board, or stores the description the user pastes.

**Account setup:** Apple Calendar needs no app registration: macOS asks once for permission to read
calendars. Registering your own Google Cloud and Microsoft apps is needed only for mail and for the
Post-MVP direct calendar sync. Google restricts apps left in testing mode, and their sign-ins can
expire after about a week; the setup guide will cover this when those features are built.

**Adding a source** means writing one adapter class with tests against saved sample responses. No
other code changes.

## Model routing and privacy tiers

Every model call goes through one router, `llm/`. The router picks a model by three things: how
hard the task is, how sensitive its data is, and cost. The routing table is a config file, and the
evaluation set decides what goes in it.

### Privacy tiers

Every call is tagged with the most sensitive data it contains. The router refuses to send a
local-only call to a cloud model. That refusal lives in code with its own tests, so it doesn't
depend on anyone remembering the rule.

| Tier | Examples | Allowed destinations |
| --- | --- | --- |
| Public | Job postings, company information | Any model |
| Personal | Resume, profile, preferences | Cloud or local (this is what employers see anyway) |
| Sensitive | Email bodies, calendar details, contacts' personal details | Local models only. Cloud calls get only minimal derived facts, such as "applied to Acme on Oct 3" |

Transcripts, and recruiter names that appear in public postings, count as Personal. Before any
Personal-tier call, the user's name and contact details (email, phone, links, and on transcripts
student IDs, birth date, and address) are removed: the model doesn't need them. A provider whose
terms allow training on API data, such as a free Gemini key, sees only Public data.

Most sensitive work needs no model at all. Matching contacts to companies is a database join, and
free time comes from free/busy data. A specific contact's details go to a cloud model only when the
user asks for an outreach draft to that person.

### Task routing

| Task class | Examples | Default route |
| --- | --- | --- |
| No model | Hard filters, deduplication, network joins, free-time math | Plain code |
| Bulk extraction | Posting to structured fields, email classification | Small, cheap model; a local model overnight |
| Judgment | Fit score with explanation, company summary | Mid-tier model |
| High stakes | Tailoring strategy and outreach drafts for the top 5–10 jobs, weekly strategy | Top-tier model |
| Conversation | Preference and rubric changes, "why is this ranked here?" | Mid-tier model; sees at most Personal data; can only create proposals |

Default routes come from the built-in model set and the providers the user has switched on: Public
tasks go to the cheapest active model, Personal tasks to the cheapest active model that doesn't
train on API data (with a stronger one as escalation), and Sensitive tasks stay local. The user can
override any route in settings.

### Scoring rubric

Fit scoring follows a rubric the user can read and change. It is a versioned file of criteria (for
example field, level, location, company stage, and learning potential), each with a weight and a
plain-English description. The scorer reads it on every run.

The Conversation panel proposes changes to the rubric as diffs. An accepted change bumps the
version and runs `hub eval`, so the user can see whether ranking improved.

### Router requirements

- **Providers:** Anthropic, OpenAI, Google, and local models, behind one interface: thin in-house
  adapters over HTTPS, with no vendor SDKs or LiteLLM between the privacy check and the network
  (ADR-0007).
- **Validated output:** every response is checked against a Pydantic schema. If it fails, the call
  is retried once, then escalated to a stronger model. Only validated answers are cached.
- **Caching:** responses are cached by prompt and input, so reruns don't cost money. An entry
  expires 30 days after it was last used, and the user can clear the cache.
- **Cost tracking:** cost is logged per run, with a monthly budget that pauses non-essential calls
  when reached; essential calls pause at twice the budget (ADR-0020).
- **Choosing models:** a new model earns a route by passing that task's evals at lower cost.

## Security

The design assumes four threats, each with a specific defense:

- **A stolen laptop:** data is encrypted on disk.
- **A leaked secret:** keys and tokens never live in files or the repo.
- **Malicious text in postings or emails (prompt injection):** model output can never trigger an
  outbound action.
- **Another website reaching the local app:** the app accepts only local, authenticated requests.

| Area | Requirement |
| --- | --- |
| Secrets | API keys and app sign-in tokens stored in the macOS Keychain through the `keyring` library. Never in config files, environment files, or logs |
| Data at rest | SQLite encrypted with SQLCipher. Its key lives in the Keychain. FileVault encrypts the disk underneath |
| Access scopes | Read-only access to mail and calendar; free/busy only where the provider supports it; where it doesn't (Apple Calendar), only event times and availability are read. The only send permission is the optional escalation email to the user |
| Sending | The email sender accepts exactly one recipient, taken from settings. Tests prove any other address is rejected |
| Prompt injection | Postings, emails, and web pages are untrusted data. The pipeline has no tools that send, delete, or submit, and model output is parsed as data, never run as instructions |
| Local web app | Listens on the local machine only, requires a session token, checks the request's host and origin, and protects forms against cross-site requests |
| Repository | Data folder outside the repo. A .gitignore guards data and secret files. A pre-commit secret scanner (gitleaks) blocks secrets, and the same scan runs in CI |
| Logs | Personal details are redacted before writing. Logs rotate and stay local |
| Dependencies | Versions pinned with a lockfile (uv); automated update PRs on GitHub |
| Model providers | Settings record each provider's data-retention terms. Providers that train on API data are disallowed for the Personal tier |
| Backups | Nightly backup of the encrypted database to a local folder. Backups stay encrypted with the same key. Restoring is tested once per release |

## Core data model

Fourteen entities hold everything, defined once as SQLAlchemy models in `core/` (Pydantic
validates model output, settings, and user files; ADR-0002). People, companies, and interactions
are shared across modules. Job-specific entities belong to the Jobs module.

| Entity | Key fields | Owner |
| --- | --- | --- |
| Profile | Identity, education, skills, interests (stated and inferred), hard constraints, work modes (remote, hybrid, onsite), timeline, weekly time budget, resume and transcript (any common format, read into facts the user confirms) | Core |
| Company | Name, aliases, domains, ATS board IDs, size, industry, notes. Names are matched loosely ("Microsoft", "Microsoft.com", and "Microsoft Corporation" are one company) | Core |
| Person | Name, company, title, schools, how you know them, source (LinkedIn export, alumni list, mail), last contact | Core |
| Interaction | Person, date, channel, summary, follow-up due | Core |
| Source | Type, configuration, approval status, last fetch, health | Core |
| Job | Company, title, URL, source, first seen, last seen, raw text, structured fields, status (new, stale, closed) | Jobs |
| Assessment | Job, filter results, fit score, odds, urgency, explanation, model used, prompt version | Jobs |
| Application | Job, date, resume variant, referrer, status (interested, reached out or referred, applied, interviewing, offer, rejected, withdrawn, ghosted), next step, follow-up due | Jobs |
| Resume variant | Base resume version, changes applied, rendered PDF path | Jobs |
| Feedback | Job, reaction, reason, date | Jobs |
| Plan | Week, time budget, planned actions, completion | Jobs |
| Run log | Run type, start and end, items processed, cost, errors | Core |
| Scoring rubric | Version, criteria (name, weight, description), changed by, date | Jobs |
| Proposal | Source (conversation or agent), target (preferences, rubric, or source list), diff, status (pending, accepted, rejected), date | Core |

Rules that apply to every entity:

- Every model-produced field stores the model and prompt version that produced it, so results can
  be traced and re-run.
- Raw source data is kept next to structured fields, so extraction can be redone when prompts
  improve.
- Schema changes go through migrations (Alembic), never by hand.
- **Resume:** the resume can be any common format (PDF, Word, LaTeX, text). When it is LaTeX, its
  content moves into a structured file that a LaTeX template renders, so variants are small,
  readable diffs in git; other formats get tailoring notes but not generated variants.

## Evaluation and quality

A hand-labeled evaluation set is the only way to know whether ranking is good. It gates every
change to prompts, models, or scoring.

- **The set:** about 30 real postings, each labeled great, okay, or no by the user, with a one-line
  reason. Revisited bi-weekly (every other week), and whenever the user's interests shift.
- **Extraction evals:** about 20 postings with hand-checked structured fields. Measures field-level
  accuracy for level, location, visa, and deadline.
- **Ranking evals:** how well the scores order the labeled set, plus how often a "no" lands in the
  top 10.
- **Filter evals:** hard filters must never drop a posting labeled "great." Any such drop is a
  failing test.
- **One command:** `hub eval` runs everything and prints a comparison with the last run, including
  cost.
- **Feedback as data:** reactions from Today become candidate labels for the next revision of the
  set.
- **Spot checks:** each week, the user reads the explanations for two or three top-ranked jobs and
  flags any that are wrong.

## MVP scope and acceptance criteria

The MVP is done when the user can spend 10 minutes a day on Today and trust that nothing important
was missed.

### Milestones

- **Milestone A, ranked shortlist (build steps 1–6):** a ranked, explained shortlist written each
  night to a local HTML page the user bookmarks, built from the templates Today will reuse. It is
  useful on its own while the interface is built.
- **Milestone B, full MVP (steps 7–10):** network paths, advice, planning, the web app, and
  scheduled runs.

Target dates are set once Nicholas confirms his weekly build hours.

### In scope

- **Shared core:** data models, encrypted storage, Keychain secrets, settings, logging, run log,
  nightly backup
- **Model router:** cloud and local providers, privacy tiers, validated output, caching, cost
  budget
- **Profile:** profile and preference files with hard constraints; resume and transcript in common
  formats (PDF, Word, LaTeX, text), read and turned into structured facts the user confirms;
  LinkedIn connections import; the scoring rubric file
- **Sources:** Greenhouse, Lever, and Ashby feeds for a target company list; The Muse as the broad
  source; jobs added by pasting a link; source discovery limited to existing source types, with
  approval; deduplication; stale and ghost detection
- **Matching:** extraction including eligibility and a job summary, eligibility filters, rubric-based
  fit score with explanation, urgency from deadlines and recruiting timelines, one exploration pick
  a day by default
- **Network:** who you know at each company, plus alumni from lists the user provides, connections
  the user tags, and anyone known to share the user's school
- **Advice:** written tailoring notes for the top jobs
- **Planning:** free/busy from Apple Calendar (including Google and Outlook accounts added on the
  Mac) or calendar files, a time budget, a weekly plan
- **Interface:** local web app with Today, Plan, Tracker, Job detail, Conversation panel, and
  System status; menu-bar count and error state; morning notification; escalation email to self,
  off by default
- **International jobs (Milestone B):** places stored as city, region, and country; an optional
  grouping by country, then city, with ranked order as the default; work-visa flags for countries
  the user can't already work in. Advice only: the app never says which visa someone qualifies for
- **Quality:** the evaluation set and `hub eval`
- **Repo:** public GitHub repo with CI, docs, and a setup guide

### Acceptance criteria

- [ ] A fresh clone on a Mac goes from setup guide to first digest in under 30 minutes.
- [ ] The overnight run finishes without errors on 5 consecutive nights.
- [ ] On the evaluation set, no posting labeled "great" is filtered out, and the top 10 contains at
      most one "no."
- [ ] Every ranked job shows an explanation the user agrees with in a weekly spot check.
- [ ] A Sensitive-tier call to a cloud model is refused, and a test proves it.
- [ ] No secret or personal data appears in the repo or the logs; the CI secret scan passes.
- [ ] Tracker shows every application with a follow-up date, and overdue follow-ups appear on Today
      and in the menu-bar count.
- [ ] Model spending for a month stays within the user's budget setting.
- [ ] No source is added without the user's approval, and a test proves it.
- [ ] A preference or rubric change made in the Conversation panel appears as a proposal, takes
      effect only when accepted, and triggers an eval run.
- [ ] A failed nightly run shows on the menu-bar icon and on Today the next morning.
- [ ] Today never shows more than 5 ranked jobs, 2 exploration picks, and 5 follow-ups.
- [ ] Milestone A: the nightly shortlist page updates on 5 consecutive nights, and every job on it
      has an explanation.

## Post-MVP roadmap

Post-MVP work comes in five phases. Jobs gets deeper first, then the interface, then a second
module, then packaging so other people can use it.

1. **Jobs depth**
   - Application status detected from Gmail (local model, Sensitive tier)
   - Outreach drafts with etiquette limits
   - Scoring and exploration picks tuned by feedback
   - LaTeX resume variants generated for the user to accept
   - Alumni from school directories, where their terms allow it
   - Contacts missing from a newer LinkedIn export flagged as no longer connected (kept, never
     deleted) and left out of warm paths
   - Your own contacts (a vCard export, or Apple Contacts as an opt-in on the Mac) as a network
     source. Sensitive tier: never sent to a cloud model, and phone numbers and emails never leave
     the Mac. Most address-book entries have only a name and a phone or email, with no employer or
     school, and many names repeat, so they mostly add value by linking to people already known
     from LinkedIn or alumni lists (matched carefully) and by the user tagging them
   - Suggested calendar blocks, added only after the user approves
   - Direct Google Calendar and Outlook sync (free/busy only), for calendars not on the Mac
   - Job-alert emails (LinkedIn, Handshake) and more applicant-tracking systems as sources
2. **Interface**
   - Full dashboard
   - Profile and preferences as a form instead of files
   - The Ask chat pane
   - Interview prep packs
3. **News module**
   - Sources the user picks
   - Related stories linked to each other
   - Coverage from several perspectives side by side
   - Background on how a story developed
   - News about target companies shown on the job and company pages
4. **Distribution**
   - Packaged Mac app with guided setup
   - Onboarding for users who aren't developers
   - Documentation for contributors
5. **Later modules**
   - Grad Schools, Colleges, Explore, and Hobbies (its own module)
   - A hosted version, only if the security and legal burden of holding other people's data is
     worth taking on

## Build order and engineering practices

The build goes bottom-up in ten steps. Each step ends with something the user can run and inspect,
and its tests pass before the next step starts.

1. **Repo and core:** project layout, data models, encrypted storage, Keychain secrets, settings,
   logging, backup. *Starts with* a spike on SQLCipher with SQLAlchemy and Alembic. *Done when* a
   test writes and reads an encrypted record.
2. **Model router:** providers, privacy tiers, schema validation, caching, cost log. *Done when* a
   Sensitive call to a cloud model is refused in a test.
3. **Profile:** load the profile files, resume, transcript (any common format), LinkedIn
   connections export, alumni lists, and the scoring rubric. *Done when* `hub profile show` prints
   a parsed profile.
4. **Sources:** the first job-board adapter (Greenhouse), normalization, deduplication, and the
   approval flow for proposed sources; then Lever, Ashby, The Muse, pasted links, and the target
   company list. *Done when* one company's postings are in the database and a rerun adds no
   duplicates.
5. **Understand:** extraction into structured fields, plus the extraction evals. *Done when* field
   accuracy is measured.
6. **Prioritize:** hard filters, rubric-based scoring, explanations, the evaluation set,
   `hub eval`, and a nightly static report run by a basic launchd job. *Done when* ranking evals
   produce a baseline and the report is readable. **Milestone A: ranked shortlist.**
7. **Network:** match connections and alumni lists to companies. *Done when* each ranked job lists
   known contacts.
8. **Advise and plan:** tailoring notes, calendar providers (Apple Calendar, calendar files),
   weekly plan. *Starts with* a spike on Apple Calendar access from the background job. *Done when*
   a plan is generated from real free/busy data.
9. **Interface:** web app (Today, Plan, Tracker, Job detail, Conversation panel, System status),
   menu-bar helper, notification, optional email. *Starts with* wireframes (done, CH-32), a visual
   design direction agreed with Nicholas (CH-105), and a spike on macOS notifications. *Done when*
   the user completes a daily review without a terminal.
10. **Schedule:** harden the launchd jobs (power and idle conditions, retries, failure alerts) and
    run the full overnight pipeline. *Done when* 5 consecutive nightly runs succeed.
    **Milestone B: full MVP.**

### Practices

- **Stack:** Python 3.12+, uv, Pydantic, SQLAlchemy typed models on SQLCipher (ADR-0002),
  Alembic, FastAPI with Jinja templates and HTMX, Typer for developer commands, pytest, Ruff, mypy,
  rumps for the menu bar.
- **Repo layout:**
  - `core/`, `llm/`, `providers/`, `profile/`
  - `modules/jobs/` with `discover/`, `understand/`, `prioritize/`, `network/`, `advise/`, and
    `plan/`
  - `web/`, `menubar/`, `evals/`, `docs/`
- **Documentation:** every module has a README covering what it does, its inputs and outputs, and
  how to run it alone. Every public function has a docstring. Design decisions are recorded as
  short architecture decision records in `docs/decisions/`.
- **Tests:** unit tests on saved sample data (no real accounts in CI). Recorded HTTP responses for
  providers. Model calls replaced by fixtures, except in `hub eval`.
- **GitHub:** public repo, MIT license (decided in CH-8), CI running lint, types, tests, and the
  secret scan on every PR. Small PRs, one step or sub-step each. A ruleset protects `main`: changes
  arrive only through pull requests whose checks pass, with no force pushes or deletion.

## Risks and open questions

The biggest risk is timing: the builder's own search runs in parallel with building the tool. The
MVP has to be useful within weeks, not months.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Building competes with searching | Peak recruiting passes before the tool helps | Milestone A delivers a ranked shortlist after step 6, while the interface is built |
| MVP keeps growing | Milestones slip | Milestone A ships first. New ideas go to the backlog, and Priya decides with Nicholas |
| Limited data access (LinkedIn, Handshake, alumni, some job boards) | Gaps in coverage | Official exports, public feeds, and user-provided alumni lists; pasting a job link from anywhere; companies on unsupported applicant-tracking systems are listed so the gap is visible |
| Account setup friction (app registration, expiring test sign-ins) | Calendar features break or setup stalls | The MVP calendar is Apple Calendar, which needs no app registration; direct Google and Microsoft sync is Post-MVP. Step-by-step setup guide |
| Filters too strict | Good jobs never shown | Only eligibility and explicit constraints filter; loose matching for company names, titles, and places; anything uncertain is shown with a flag, not hidden |
| SQLCipher doesn't work cleanly with SQLAlchemy and Alembic | Encryption delayed | Spike in step 1; fallback to an encrypted APFS volume. *(Resolved: it works, ADR-0001.)* |
| Ranking feels wrong | User stops trusting the digest | Evaluation set, rubric, explanations, weekly spot checks |
| Model costs creep up | Budget overrun | Caching, cheap models first, a hard monthly budget |
| Sensitive data sent to the cloud by mistake | Privacy breach | Tier enforcement in code, with tests |
| Scope creep into News and other modules | MVP never ships | News starts only after the MVP acceptance criteria pass |

### Open questions

- [x] Broad job source for the MVP: The Muse (CH-10, Oct 21; its terms are checked when the
      adapter is built, CH-95)
- [x] License: MIT (CH-8)
- [x] Exploration share: 1 pick a day by default, up to 2
- [x] Hobbies: its own module
- [x] Escalation email: off by default
- [x] Recruiter names from public postings: Personal tier
- [x] Weekly build hours: Claude builds overnight, midnight to 8 a.m. Eastern (CH-5)
- [ ] Which alumni lists (clubs, career office) are available to import (CH-9)
- [ ] The interface's visual design: look, type, color, navigation (CH-105)
