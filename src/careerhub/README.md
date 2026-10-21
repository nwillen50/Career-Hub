# `careerhub`

| Module | What it does | Ticket |
| --- | --- | --- |
| `cli` | The `hub` command: `profile show`, `sources ...`, `ingest greenhouse`, `shortlist build`, `nightly` | CH-57, 60, 61, 69, 70 |
| `schedule` | Renders and writes the launchd job (`hub schedule install`); the user starts it | CH-71 |
| `pipeline` | `run_nightly`: ingest, understand, prioritize, write the page, back up | CH-70 |

`hub nightly` is what the scheduler runs. Problems in one source or job don't stop the others; the
run is marked failed only when nothing useful happened (every source failed, or the page couldn't
be written). See [ADR-0020](../../docs/decisions/0020-nightly-run.md).

## Scheduling (needs a Mac to finish)

`hub schedule install` writes `~/Library/LaunchAgents/com.careerhub.nightly.plist` for the
`nightly_run_time` setting and prints the `launchctl` command; it does not start the job. See
[ADR-0021](../../docs/decisions/0021-launchd-schedule.md).
