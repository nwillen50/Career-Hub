# `careerhub`

| Module | What it does | Ticket |
| --- | --- | --- |
| `cli` | The `hub` command (run `hub` with no arguments for every command): profile, sources, ingest, targets, add-job, companies, models, cache, people and alumni, plan, shortlist, nightly, schedule | CH-57 to CH-112 |
| `schedule` | Renders and writes the launchd job (`hub schedule install`); the user starts it | CH-71 |
| `pipeline` | `run_nightly`: ingest, understand, prioritize, write the page, back up | CH-70 |

`hub nightly` is what the scheduler runs. Problems in one source or job don't stop the others; the
run is marked failed only when nothing useful happened (every source failed, or the page couldn't
be written). See [ADR-0020](../../docs/decisions/0020-nightly-run.md).

## Scheduling (needs a Mac to finish)

`hub schedule install` writes `~/Library/LaunchAgents/com.careerhub.nightly.plist` for the
`nightly_run_time` setting and prints the `launchctl` command; it does not start the job. See
[ADR-0021](../../docs/decisions/0021-launchd-schedule.md).

A light midday job is available too: `hub refresh` fetches sources, reads new postings with free or
local models only, filters, and rewrites the shortlist page (no scoring, tailoring or backup). Set
`midday_refresh_on` to `true` (time: `midday_refresh_time`, default 12:30) and run
`hub schedule install` to write `com.careerhub.refresh.plist`; starting it is still your call.
