# `careerhub.modules.plan`

Free time and the weekly time budget become suggested work blocks (build step 8, epic CH-29).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `freebusy` | `Interval`, `FreeBusySource`, `merge`, `free_windows`, `daily_windows` | CH-76 | Built |
| `ics` | `IcsCalendar`: busy time from an exported calendar file | CH-76 | Built |
| weekly plan | Suggested blocks for the week | CH-77 | Planned |

Only *when* you are busy is read from a calendar: event titles, places, and attendees are never read
or kept. Put the path of an exported `.ics` file in the `calendar_files` setting and your
`timezone` (for example `"America/New_York"`) in `settings.json`. Live Google and Outlook sync
needs app registration (CH-30). See [ADR-0026](../../../../docs/decisions/0026-free-busy-from-calendar-files.md).
