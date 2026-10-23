# `careerhub.modules.plan`

Free time and the weekly time budget become suggested work blocks (build step 8, epic CH-29).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `freebusy` | `Interval`, `FreeBusySource`, `merge`, `free_windows`, `daily_windows` | CH-76 | Built |
| `ics` | `IcsCalendar`: busy time from an exported calendar file | CH-76 | Built |
| `weekly` | `plan_week`, `build_tasks`: suggested blocks inside the time budget | CH-77 | Built |
| `suggest` | `suggest_week`: settings, calendar files, and the shortlist into a plan | CH-77 | Built |

Only *when* you are busy is read from a calendar: event titles, places, and attendees are never read
or kept. Put the path of an exported `.ics` file in the `calendar_files` setting and your
`timezone` (for example `"America/New_York"`) in `settings.json`. Live Google and Outlook sync
needs app registration (CH-30). See [ADR-0026](../../../../docs/decisions/0026-free-busy-from-calendar-files.md).

## The weekly plan

`hub plan` suggests work blocks for the next seven days: apply to the top jobs (90 minutes) and
message someone you know there first (30 minutes), soonest deadline first, in your free time, within
the `weekly_time_budget_hours` in `preferences.yaml`, and never more than half the budget on one day.
It only suggests: nothing is put on a calendar or sent. Tasks that don't fit are listed with the
reason. See [ADR-0027](../../../../docs/decisions/0027-weekly-plan.md).

## Apple Calendar (CH-83)

Set `use_apple_calendar: true` in `settings.json` (and optionally `apple_calendars: ["Home"]`; empty
means all) and the plan avoids times your Mac's Calendar app shows as busy. To include Google or
Outlook calendars, add the account in System Settings > Internet Accounts with Calendars turned on.
macOS asks for permission once; allow the terminal (or app) you run `hub` from under Privacy &
Security > Calendars. Career Hub reads only each event's start, end, all-day flag, availability, and
status (never the title, notes, place, link, or attendees), skips events marked free and cancelled
ones, and falls back to your `.ics` files with a clear message if access is denied. Mac only. See
[ADR-0040](../../../../docs/decisions/0040-apple-calendar.md).
