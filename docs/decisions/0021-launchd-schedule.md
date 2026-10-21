# 0021. Scheduling with launchd: write the job, let the user start it

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-71, part of CH-27 (epic CH-25, step 6)
- Deciders: Leo (owner), Sam (reviewer), Nicholas (approver)

## Context

Milestone A needs the pipeline to run every night "by a basic launchd job". launchd is macOS's own
scheduler and needs no extra software.

## Decision

1. **`hub schedule install` writes `~/Library/LaunchAgents/com.careerhub.nightly.plist` and prints
   the `launchctl` commands to start and stop it. It does not run them.** Starting a recurring
   background job on the user's Mac is the user's call; this also keeps the code free of anything
   that acts on the machine beyond writing one clearly named file.
2. **The job runs `<venv python> -m careerhub.cli nightly`**, so it doesn't depend on the shell's
   PATH or on `hub` being installed globally. Output goes to `logs/nightly.out.log` and
   `nightly.err.log` in the private data folder; the run's own output holds only counts and type
   names (ADR-0020).
3. **Time comes from the `nightly_run_time` setting** (default 02:00), validated as 24-hour HH:MM.
   launchd runs a job missed during sleep once the Mac wakes.
4. **Built with `plistlib`** (standard library), so paths with special characters stay valid.
5. The job runs at low priority (`Background`, `Nice 10`, low-priority I/O) and does not run at
   load, so installing it never triggers a run.

## Evidence

`tests/test_schedule.py`: plist contents, special characters, time validation, install writes with
mode 644 and replaces an older file, the printed commands, and the command end to end with a
temporary home folder.

## Alternatives considered

- **Run `launchctl` ourselves.** Convenient, but loads a persistent job without an explicit user
  step, and can't be tested off a Mac.
- **cron.** Deprecated on macOS and doesn't catch up after sleep.

## Consequences

- **Still needs a Mac session:** run `hub schedule install`, then the `launchctl bootstrap` line, and
  check the next morning that `shortlist/index.html` updated (the "5 consecutive nights" check).
- If the venv moves, run `hub schedule install` again to refresh the path.
