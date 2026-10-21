# 0026. Free/busy from calendar files first

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-76, part of CH-29 (step 8)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

The weekly plan needs to know when the user is free. The PRD wants Google Calendar and Outlook
free/busy, with free/busy only (no event titles). Live access needs each user to register a Google
Cloud and a Microsoft app, which the setup spike (CH-30) covers and which needs Nicholas's accounts.

## Decision

1. **A `FreeBusySource` interface** returns busy `Interval`s only. Intervals hold two timezone-aware
   times and nothing else: no title, place, or attendee exists in the type.
2. **First source: iCalendar files (`IcsCalendar`).** Google Calendar and Outlook can both export a
   file or publish a private feed; reading a file needs no sign-in and no registered app. Live sync
   adapters (CH-30) can implement the same interface later.
3. **Reading rules:** repeating events are expanded (`recurring-ical-events`); events marked free
   (`TRANSP:TRANSPARENT`) and cancelled events don't count; an all-day event marked busy blocks the
   whole local day; times without a timezone are read in the user's timezone.
4. **Settings:** `timezone` (IANA name, validated) and `calendar_files` (paths).
5. **Dependencies:** `icalendar` and `recurring-ical-events` (hand-rolling recurrence rules is a
   well-known source of bugs), plus `types-icalendar` for the type checker; a mypy override covers
   `recurring_ical_events`, which ships no type information.
6. Free time = working windows (per local day, converted to UTC so daylight saving is right) minus
   busy time, dropping gaps shorter than 30 minutes.

## Evidence

`tests/modules/plan/test_freebusy.py`: interval validation, merging, subtraction, short gaps,
daylight-saving window math, event kinds (UTC, zoned, repeating, free, cancelled, all-day, holiday,
floating), clipping, no text exposed, bad and missing files. Writing it found a real bug: an event
touching the query range could build a backwards interval; now skipped.

## Alternatives considered

- **Wait for OAuth.** Blocks the plan on accounts and a spike.
- **Write the iCalendar parser.** Recurrence and timezone rules are subtle; use the maintained libs.

## Consequences

- The user has to export a calendar file (or use a private feed URL saved as a file) until live
  sync exists; the file is read locally and its event text is never read or stored.
