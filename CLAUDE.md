# Career Hub: instructions for Claude

Career Hub is a local-first Python app for macOS that finds full-time jobs, ranks them with
explanations, maps warm paths through the user's network, and plans the week around available time.
It drafts and advises; it never sends, submits, or applies on the user's behalf.

Read this file, then `README.md`, `CONTRIBUTING.md`, `docs/decisions/`, and the README of any
package you touch before writing code.

## Where things live

- **PRD (the plan):** https://claude.ai/code/artifact/a23c912b-3a47-4702-9c63-607a6602c316
  (a Claude Docs document with PRD, Team, and Review log tabs). Don't edit it; propose changes as a
  ticket owned by Nicholas.
- **Board (the tickets):** https://claude.ai/artifact/CGHqFtxSf8BQ5suX27CbLM. Tickets are
  `CH-<n>`, stored in the artifact's `tickets` collection with fields: key, title, type
  (epic|story|task|spike|bug), status (backlog|todo|in_progress|in_review|done), owner
  (Priya|Sam|Leo|Nicholas), priority (P0-P3), epic, milestone (A|B|""), blocked, blockedReason,
  description, notes, created, updated. If this session can't reach the board, say so and list
  the ticket changes Nicholas should make.
- **Repo:** github.com/nwillen50/Career-Hub. Nicholas reviews and merges every PR.

## The team

Nicholas is the product owner and the only person who merges. Claude plays three personas:

- **Priya, Senior PM:** scope, acceptance criteria, metrics, roadmap, board accuracy.
- **Sam, Senior SWE:** core, model router, providers, pipeline, scheduling, security, CI, migrations.
- **Leo, Front-end engineer:** web app, menu-bar helper, notifications, accessibility.

A ticket is built by its owner persona and reviewed by a different persona before its PR opens.

## Build order

Bottom-up. Never build ahead of what a ticket needs. Epics, in order:

| Step | Epic | What | Milestone |
| --- | --- | --- | --- |
| 1 | CH-11 | Repo and core: models, encrypted storage, secrets, settings, logging, backup | A |
| 2 | CH-16 | Model router: providers, privacy tiers, validation, caching, cost | A |
| 3 | CH-18 | Profile: resume, transcript, preferences, LinkedIn export, rubric | A |
| 4 | CH-21 | First job source, dedupe, source approval | A |
| 5 | CH-23 | Understand: postings into structured fields | A |
| 6 | CH-25 | Prioritize: filters, scoring, explanations, evals, nightly shortlist | A ends |
| 7 | CH-28 | Network: contacts and alumni per company | B |
| 8 | CH-29 | Advise and plan: tailoring notes, calendars, weekly plan | B |
| 9 | CH-31 | Interface: web app, menu-bar icon, notifications | B |
| 10 | CH-34 | Schedule: hardened nightly runs | B ends |

Within an epic: spikes first, then P0 to P3, then key number. Skip blocked tickets and tickets
owned by Nicholas.

## Cloud or Mac

Sessions run either in the cloud (the nightly build, Linux) or on Nicholas's Mac (Claude Code
locally). Check which you are in with `uname -s`: `Darwin` means the Mac, `Linux` means the cloud.

- **Cloud sessions** take platform-neutral work. They must not depend on macOS: use test doubles
  for the Keychain and launchd, and rely on CI's macOS jobs to prove Mac behavior.
- **Mac sessions** take what needs a real Mac: Keychain integration, launchd jobs, the menu-bar
  helper, notifications, and running the app on Nicholas's real data.
- **Real personal data never leaves the Mac.** It lives in
  `~/Library/Application Support/CareerHub`. Never copy it into the repo, a test, a log, a PR,
  the board, or a cloud session.

## Working a ticket

1. Set the ticket to `in_progress` with a note.
2. Branch from `main` (or from an unmerged dependency, noting "Depends on #N" in the PR) as
   `ch-<n>-short-description`. **Every PR targets `main`**, even one built on another PR's
   branch: a PR aimed at another PR's branch merges into that branch, not `main` (bug CH-42).
   Until the dependency merges, its commits also show in the diff; say which commits to review.
3. Commit in small steps; every message starts `CH-<n>: `.
4. Write code a student can read: clear names, a docstring on every module and public function,
   comments for anything non-obvious, a current README in each package.
5. Test every behavior with saved sample data only. No real accounts, personal data, or network
   access in tests.
6. Before pushing, all of these must pass:
   ```bash
   uv sync --group dev
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy
   uv run pytest
   ```
   Fix failures; never weaken a check to get past it.
7. Record design decisions as ADRs in `docs/decisions/` (template in its README) and update the index.
8. Review as a second persona: read the whole diff, list concrete findings, fix them.
9. Open a PR titled `CH-<n>: <title>` describing what changed, how it was tested, review
   findings, and what Nicholas should look at closely.
10. Move the ticket to `in_review` with the PR link in its notes.

Keep each PR reviewable: under about 400 changed lines including tests. Split larger tickets on
the board first. If 3 or more PRs are unmerged, don't start new feature work; strengthen what's
open instead.

## Questions, blockers, bugs

- Need a decision from Nicholas? When he's present, ask. When working unattended, create a task
  ticket owned by Nicholas with the question, the options, and a recommendation; mark the
  dependent ticket blocked, naming that ticket; move on.
- Bugs get a bug ticket with steps to reproduce, severity, and the source ticket. Every fix
  includes a test that failed before it.

## Never

- Merge a PR, push to `main`, force-push, or rewrite published history.
- Commit personal data, API keys, tokens, or credentials.
- Add code that sends, submits, deletes, or applies on the user's behalf.
- Call a model anywhere except through the model router (`careerhub.llm`, from step 2 on).
- Edit the PRD or delete tickets.
