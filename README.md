# Career Hub

Career Hub is a local-first assistant for career decisions. Its first module, **Jobs**, finds
full-time roles, ranks them with explanations, maps warm paths through your network, and plans your
week around the time you actually have.

It runs on your Mac. Your data stays on your machine, encrypted. It drafts and advises, but it never
sends, submits, or applies on your behalf.

> **Status:** early build, step 1 of 10 (repo and core). Nothing here is usable yet.

## How this project is built

Career Hub is built **bottom-up**: each module runs and passes its own tests before anything is built
on top of it. The plan, scope, and design live in the [PRD](docs/prd/prd.md); every
piece of work is a ticket (`CH-1`, `CH-2`, ...) on the project board.

| Step | What | Milestone |
| --- | --- | --- |
| 1 | Repo and core: data models, encrypted storage, secrets, settings, logging | A |
| 2 | Model router: providers, privacy tiers, validation, caching, cost | A |
| 3 | Profile: resume, preferences, LinkedIn connections, scoring rubric | A |
| 4 | First job source, deduplication | A |
| 5 | Understand: postings into structured fields | A |
| 6 | Prioritize: filters, scoring, explanations, nightly shortlist | **A: ranked shortlist** |
| 7 | Network: who you know at each company | B |
| 8 | Advise and plan: tailoring notes, calendar, weekly plan | B |
| 9 | Interface: local web app and menu-bar icon | B |
| 10 | Schedule: hardened nightly runs | **B: full MVP** |

## Repository layout

```
src/careerhub/        the application, one package per layer
  core/               data models, encrypted storage, secrets, settings, logging (step 1)
  llm/                the model router: privacy tiers, providers (step 2)
  profile/            profile and preference files, resume, LinkedIn import (step 3)
  modules/network/    alumni import, people tags, warm paths (step 7)
  modules/plan/       free time from calendar files, the weekly plan (step 8)
  modules/jobs/       find, store, and (later) understand and rank postings (steps 4 to 6)
  prompts/            versioned prompt files, one per model task
  web/                pages for the user: the nightly shortlist now, the web app later (step 9)
tests/                tests, mirroring src/
spikes/               short experiments that answer one question before we commit to a design
docs/prd/             the product requirements (prd.md) and the team (team.md)
docs/decisions/       architecture decision records (why things are the way they are)
CLAUDE.md             instructions every Claude session follows: team, workflow, rules
```

Packages for later steps (`llm/`, `providers/`, `profile/`, `modules/jobs/`, `web/`, `menubar/`)
are added when their step starts, not before.

## Development

Requires macOS (or Linux for development), Python 3.12+, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev          # install dependencies
uv run pre-commit install    # turn on the secret scanner and formatters for every commit
uv run pytest                # run tests
uv run ruff check .          # lint
uv run mypy                  # type-check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how work flows from ticket to merge.

## Privacy

- Personal data lives in `~/Library/Application Support/CareerHub`, never in this repository.
- The database is encrypted with SQLCipher; its key lives in the macOS Keychain.
  See [ADR-0001](docs/decisions/0001-encrypted-storage.md).
- A secret scanner runs before every commit and in CI.

## License

[MIT](LICENSE).
