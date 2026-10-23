# Mac session checklist

Checks that only work on Nicholas's real Mac. Cloud sessions build everything else with test doubles
(Keychain, launchd, EventKit, notifications) and rely on CI's macOS jobs. Run these in order in a
local Claude Code session (`uname -s` prints `Darwin`). Never copy anything from
`~/Library/Application Support/CareerHub` into the repo, a PR, a log, or the board.

Record each result on the matching ticket or ADR. Tick the box when it passes.

## 0. Setup

- [ ] `git pull` on `main`, then `uv sync --group dev`.
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` all pass.

## 1. Keychain (core, ADR-0001)

- [ ] `CAREERHUB_TEST_REAL_KEYCHAIN=1 uv run pytest tests/core/test_secrets_real_keychain.py`
  passes. Success: the test creates, reads, and removes a throwaway Keychain item; a prompt may
  appear once, click Always Allow.
- [ ] `uv run hub profile show` creates the database key on first use. Success: no error, and
  Keychain Access shows one Career Hub item.

## 2. Model keys (CH-54, owned by Nicholas)

- [ ] `uv run hub models list` then `uv run hub models add <anthropic|openai|gemini> --tier free`
  (or `paid`). It asks for the key; paste it at the prompt only. Success: the key shows up in
  Keychain Access, never in `settings.json`, never printed.
- [ ] `uv run hub models routes` shows each task on the model you expect.
- [ ] `uv run hub cache clear` prints how many saved answers it deleted.

## 3. Nightly job with launchd (ADR for CH-71)

- [ ] `uv run hub schedule install` writes `~/Library/LaunchAgents/<label>.plist` (mode 644) and
  prints two `launchctl` commands.
- [ ] Run the printed `launchctl bootstrap gui/<uid> <plist>` command. Success: no error.
- [ ] `launchctl print gui/$(id -u)/<label>` shows the job. Then
  `launchctl kickstart gui/$(id -u)/<label>` runs it now. Success: the shortlist page is
  written and the log shows a finished run with no errors.
- [ ] The job works with the laptop locked and without a terminal open (the Keychain item allows
  the job's Python).
- [ ] `launchctl bootout gui/$(id -u)/<label>` removes it cleanly.

## 4. Apple Calendar through EventKit (CH-83, ADR-0040)

- [ ] `uv run hub plan` shows the macOS calendar permission prompt on first use. Allow it.
- [ ] Success: suggested blocks avoid your real busy times; event titles are never printed or
  stored (free/busy only).
- [ ] Check whether permission granted to the terminal also works when the launchd job runs; if
  not, note the app that needs the permission.
- [ ] Check which EventKit access call exists on this macOS version
  (`requestFullAccessToEventsWithCompletion_` on macOS 14 and later). Record the result in
  ADR-0040.

## 5. End to end on real data

- [ ] Put your real `profile.md`, `preferences.yaml`, and resume in the CareerHub data folder
  (not the repo). `uv run hub profile ready`, then `uv run hub profile facts` and accept or reject
  facts.
- [ ] Import `Connections.csv` and an alumni list (`hub people import-alumni <file> --preview`
  first).
- [ ] Approve one source (`hub sources list`, `hub sources approve <id>`), then
  `uv run hub ingest greenhouse --company <board>`.
- [ ] `uv run hub nightly`, then open the shortlist page. Check explanations, warm paths, and the
  weekly plan make sense.
- [ ] `uv run hub backup` (if present) restores into an empty folder without errors.

## 6. Later (when those tickets land)

- [ ] Menu-bar helper count and error state (CH-146).
- [ ] Morning notification after the nightly run (CH-33 spike, CH-147).
- [ ] School job board saved-search emails (CH-129 and CH-128).
