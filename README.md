# Career Hub

Career Hub is a local-first assistant for career decisions. Its first module, **Jobs**, finds
full-time roles, ranks them with explanations, maps warm paths through your network, and plans your
week around the time you actually have.

It runs on your Mac. Your data stays on your machine, encrypted. It drafts and advises, but it never
sends, submits, or applies on your behalf.

> **Status (Oct 23, 2026):** steps 1 to 9 are built and merged, and most of step 10. That covers
> Milestone A (a ranked nightly shortlist) and the local web app. Nothing has run on real data or a
> real Mac yet; the Mac checks are in [`docs/mac-session.md`](docs/mac-session.md). Next: lighter
> skill matching and pay in scoring, a Browse page, tracking a company from its careers page, school
> name matching, and the rest of step 10. The live status is on the project board.

## How this project is built

Career Hub is built **bottom-up**: each module runs and passes its own tests before anything is built
on top of it. The plan, scope, and design live in the [PRD](docs/prd/prd.md); every
piece of work is a ticket (`CH-1`, `CH-2`, ...) on the project board.

| Step | What | Milestone | State |
| --- | --- | --- | --- |
| 1 | Repo and core: data models, encrypted storage, secrets, settings, logging | A | Built |
| 2 | Model router: providers, privacy tiers, validation, caching, cost | A | Built |
| 3 | Profile: resume, transcript, preferences, LinkedIn connections, scoring rubric | A | Built |
| 4 | Job sources (Greenhouse, Lever, Ashby, The Muse, pasted links), company boards, deduplication | A | Built |
| 5 | Understand: postings into structured fields | A | Built |
| 6 | Prioritize: filters, scoring, explanations, nightly shortlist | **A: ranked shortlist** | Built |
| 7 | Network: who you know at each company | B | Built |
| 8 | Advise and plan: tailoring notes, calendar, weekly plan | B | Built |
| 9 | Interface: local web app, menu-bar icon, morning notification | B | Web app built; menu-bar count and notification logic built, the menu-bar helper itself still to build on a Mac |
| 10 | Schedule: hardened nightly runs | **B: full MVP** | Mostly built (plugged-in only, retries, midday refresh, restore, failure alert); five real nights to go |

## What it does today

`hub web` opens the local web app (Today, Job detail, Tracker, Contacts, Interactions, Plan, Profile, System
status, and the Conversation panel). From the terminal, `hub` can:

| Command | What it does |
| --- | --- |
| `hub profile show` | Print your parsed profile, preferences, and what's missing (never resume or transcript text) |
| `hub profile ready`, `hub profile facts` | Check the profile and switch scoring on; review facts read from your resume and transcript (`accept`, `reject`) |
| `hub sources add`, `list`, `approve`, `reject`, `disable` | Propose a job source and decide on it; nothing is fetched until you approve |
| `hub ingest greenhouse`, `lever`, `ashby --company <board>` | Fetch one approved company's postings |
| `hub targets find-boards`, `note`, `import`, `list`, `enable`, `disable`, `enable-matching` | Companies you follow, and finding each one's job board |
| `hub add-job <url>` | Add a job from any link (LinkedIn and Handshake pages are never fetched; you give the company and title, or paste the text) |
| `hub jobs close <id>`, `reopen <id>` | Close or reopen a job you added by link (asks first; nothing is deleted) |
| `hub companies suggestions`, `merge`, `skip` | Answer suggestions about near-duplicate company names |
| `hub companies merges`, `split <company> <name>` | List merges; undo one exactly (asks first) |
| `hub models list`, `add`, `remove`, `add-custom`, `routes` | See and switch on models (keys go to the Keychain), and which model each task uses |
| `hub cache clear [--expired]` | Delete saved model answers |
| `hub nightly [--if-plugged-in]` | Run the whole pipeline once: ingest, extract, filter, score, tailor, write the shortlist page (with warm paths), back up. With the flag, a Mac on battery skips the night |
| `hub refresh` | A light midday run: new jobs and filters only, no paid models |
| `hub score --all` | Score every job again on the next nightly run |
| `hub web [--port N]` | The local web app on 127.0.0.1; prints a private link to open |
| `hub demo [--reset] [--port N]` | The web app on made-up data in its own folder (`CareerHubDemo`, next to the real one); labeled "Demo data" on every page; no model calls, network, or Keychain; `--reset` rebuilds it |
| `hub attention [--json \| --notify]` | What the menu-bar icon counts; `--notify` shows the morning notification once a day |
| `hub backup list`, `restore <file> --to <new folder>` | List backups; restore one into a new folder and check it (your live data is never touched) |
| `hub shortlist build` | Write the shortlist page to your data folder |
| `hub paths <company>` | Who you know at a company, strongest first, with reasons |
| `hub people find`, `tag`, `import-alumni` (also `hub alumni import`) | Find people, record how you know them, import an alumni list in any common layout |
| `hub alumni mappings`, `forget <file>` | The column mappings remembered per alumni file name |
| `hub plan` | Suggested blocks for the next 7 days from your free time and time budget |
| `hub schedule install` | Write the nightly launchd job (and the midday one if `midday_refresh_on`) and print the commands that start them |

Checks that need your real Mac (Keychain, launchd, calendar) are listed in
[`docs/mac-session.md`](docs/mac-session.md).

## Models and cost

Every model call goes through one router that enforces privacy tiers: job postings (Public) can go
to any model, your profile and resume (Personal) only to models that don't train on API data, and
anything Sensitive stays on your Mac. Your name and contact details are removed before any model
sees your profile or resume.

You bring your own API keys, stored in the macOS Keychain. A typical low-cost setup:

| Task | Model | Why |
| --- | --- | --- |
| Reading postings | Gemini (free tier) | Postings are public, so a free key that may train on data is fine |
| Scoring jobs, tailoring notes | Claude Haiku | Cheap (well under $2 a month here), doesn't train on API data |
| Fallback for everything | A local model through Ollama | Free and private; runs overnight |

A monthly budget setting caps spending. A built-in model catalog and `hub models` commands switch
models on with a key; the exact steps for this setup are in [`docs/model-setup.md`](docs/model-setup.md).
If a model is down, rate limited, or paused by the budget, the next one in the route is used.

## Repository layout

```
src/careerhub/        the application, one package per layer
  core/               data models, encrypted storage, secrets, settings, logging (step 1)
  llm/                the model router: privacy tiers, providers, cache, cost log (step 2)
  profile/            profile and preference files, resume, transcript, LinkedIn import (step 3)
  modules/jobs/       find, store, understand, filter, score, and tailor postings (steps 4 to 6, 8)
  modules/network/    alumni import, people tags, warm paths (step 7)
  modules/plan/       free time from calendars, the weekly plan (step 8)
  prompts/            versioned prompt files, one per model task
  web/                the nightly shortlist page and the local web app (step 9)
  pipeline.py         hub nightly and hub refresh: run every stage once
  schedule.py         the launchd jobs for the nightly and midday runs
  power.py            reads whether the Mac is plugged in (step 10)
tests/                tests, mirroring src/
evals/                evaluation sets and samples for hub eval
spikes/               short experiments that answer one question before we commit to a design
docs/prd/             the product requirements (prd.md) and the team (team.md)
docs/decisions/       architecture decision records (why things are the way they are)
docs/design/          wireframes for the interface
CLAUDE.md             instructions every Claude session follows: team, workflow, rules
```

Packages for later steps (such as the menu-bar helper) are added when their ticket starts, not before.

## Development

Requires macOS (or Linux for development), Python 3.12+, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev          # install dependencies
uv run pre-commit install    # turn on the secret scanner and formatters for every commit
uv run pytest                # run tests
uv run ruff check .          # lint
uv run mypy                  # type-check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how work flows from ticket to merge. `main` only changes
through pull requests whose checks pass.

## Privacy

- Personal data lives in `~/Library/Application Support/CareerHub`, never in this repository.
  Your input files go in its `inputs/` folder (`resume/`, `transcript/`, `linkedin/`).
- The database is encrypted with SQLCipher; its key lives in the macOS Keychain.
  See [ADR-0001](docs/decisions/0001-encrypted-storage.md).
- Model calls follow privacy tiers enforced in code, with tests (see "Models and cost").
- Career Hub never logs in to or scrapes LinkedIn or Handshake.
- A secret scanner runs before every commit and in CI.

## License

[MIT](LICENSE).
