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
- [ ] After the nightly run, `uv run hub backup list` shows a backup. Then
  `uv run hub backup restore <that file> --to ~/Desktop/ch-restore-test`. Success: it says the
  integrity check passed and the row counts match, and your live data is untouched. Delete the
  test folder afterwards. This is the once-per-release restore test in CONTRIBUTING.md.

## 6. The local web app and attention (CH-138 to CH-147)

- [ ] `uv run hub demo` makes `~/Library/Application Support/CareerHubDemo` and prints a link. Success:
  every page shows the "Demo data" strip, about 30 invented jobs are on Today and Tracker, and
  nothing in `~/Library/Application Support/CareerHub` changed. `hub demo --reset` rebuilds it.

- [ ] `uv run hub web` prints a link like `http://127.0.0.1:8765/?t=...`. Open it in your browser.
  Success: Today opens with your real top jobs; Plan, Tracker and System status open; a page
  loaded from any other address (try `http://localhost:8765/today` without the token) says to
  open the link.
- [ ] On Today, press Interested on a job. Success: the card is replaced by a note with Undo; the
  job is gone from Today after a reload and appears in the Tracker once CH-157 lands.
- [ ] System status shows your models and this month's spend.
- [ ] `uv run hub attention --json` prints the counts the menu-bar icon will show (counts only).
- [ ] `uv run hub attention --notify` shows one banner on this Mac and a second run the same day
  shows nothing. Check whether clicking it can open Today without a signed app (CH-33 spike); if
  not, the menu-bar icon alone is the fallback.
- [ ] The menu-bar helper (CH-146's Mac half): an icon with the count, a red dot after a failed
  run, and one menu item, "Open Today".

## 7. Schedule and the later web tabs

- [ ] CH-165: set `"midday_refresh_on": true` in settings.json, run `uv run hub schedule install`,
  and check it wrote both `com.careerhub.nightly.plist` and `com.careerhub.refresh.plist` and
  started nothing. Start the refresh with the `launchctl bootstrap` line it prints. Success: at
  12:30 the System status tab shows a `refresh` run, and the shortlist page
  updates without new scores.
- [ ] CH-171: on the Contacts tab, your real LinkedIn contacts appear; edit "how you know them"
  for one person who also has an `alumni:` tag. Success: the tag stays. Log a contact with a
  follow-up date for today; it shows on Today and clears when you press Done.
- [ ] CH-168: set `ghost_after_days` low (for example 1) on a copy of your data and check an old
  Applied application becomes Ghosted after `hub nightly`, and changing it back sticks.

## 8. Needs open web access (not a Mac, but not possible from the cloud sandbox either)

- [ ] CH-126: check Y Combinator's terms and robots rules for the public company directory, and run
  `hub targets find-boards` on a sample of 30 YC companies. The cloud sandbox cannot reach
  ycombinator.com, so the spike could not be done overnight.

## 9. Later

- [ ] School job board saved-search emails (CH-129, then CH-128): forward one sample alert email
  (names removed) so the parser can be written against the real layout.

## Catch-up of a skipped night (CH-181)

- [ ] Run `uv run hub schedule install`, then the printed `launchctl bootstrap` commands for the
  nightly and the catch-up jobs. Unplug the Mac and run `uv run hub nightly --if-plugged-in`.
  Success: it prints "Skipped: the Mac is on battery power", and System status shows
  "Skipped on battery at ...; no catch-up has run yet."
- [ ] Plug the Mac in before the cutoff (`catchup_until`, default 08:00) and wait up to 30 minutes
  (or run `uv run hub nightly --catch-up` yourself). Success: one full run happens, and System
  status says "Skipped on battery at ..., caught up at ...". Run `--catch-up` again: it says
  "nothing to do".

## Menu-bar helper (CH-191)

- [ ] `uv sync --extra menubar`, then `uv run hub menubar`. Success: an icon with a number appears in
  the menu bar; hovering shows words like "2 follow-ups due, 3 new top jobs". After a failed nightly
  run the text starts with "!".
- [ ] Choose **Open Today** from its menu with no `hub web` running. Success: your browser opens
  Today. Choose it again: the same page opens and no second server starts. With `hub web` already
  running in a terminal, it says to use that window's link.
- [ ] Set `"menubar_on": true` in `settings.json`, run `uv run hub schedule install`, run the printed
  `launchctl bootstrap` line for `com.careerhub.menubar`, then log out and in. Success: the icon is
  there without a terminal. Quit from its menu: it stays quit until the next login.
- [ ] Note in the CH-33 spike whether a notification can be posted from this Python process.

## Model checks and the log (CH-209)

- [ ] `uv run hub models check` prints OK for each model with a key, or the short reason
  (`HTTP 401 (key rejected)`, `HTTP 400 (often no credit)`, ...). No key or prompt text appears.
- [ ] After `uv run hub nightly`, `~/Library/Application Support/CareerHub/logs/careerhub.log`
  exists (mode 600) and names any skipped model with its reason, and has no emails or keys.

## Copy buttons on Contacts (CH-204)

- [ ] In `hub web`, open Contacts. Point at one email: only its copy icon appears (not the phone's).
  Click it, paste somewhere: the exact email. "Copied" shows for about 2 seconds.
- [ ] Tab to a phone value: its copy icon appears; press Enter; paste. With VoiceOver on, "Copied" is read out.
