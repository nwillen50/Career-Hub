# 0040. Apple Calendar as the MVP calendar provider

- Status: Accepted (needs a Mac session to confirm the permission behavior)
- Date: 2026-10-22
- Ticket: CH-83, for epic CH-29 (step 8)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

PRD (CH-82): the MVP calendar is Apple Calendar through EventKit, which also carries Google and
Outlook calendars added in System Settings > Internet Accounts. EventKit has no free/busy-only
access, so the app asks for event access and must read as little as possible.

## Decision

1. `modules/plan/apple.py` is a `FreeBusySource` over an `EventStore` seam. The only facts read from
   an event are start, end, all-day flag, availability, and status; free and cancelled events are
   skipped; the result is the same merged, clipped UTC `Interval` as the `.ics` reader. `RawEvent` has
   no field for a title, notes, location, URL, or attendees, and a test proves the real store touches
   only the five permitted calls.
2. EventKit (`pyobjc-framework-EventKit`, installed only when `sys_platform == 'darwin'`) is imported
   lazily. Off a Mac, or without pyobjc, or if access is denied or times out, the plan falls back to
   the other sources and `hub plan` prints a problem line saying what to do.
3. Settings: `use_apple_calendar` (default false: asking is a permission prompt) and
   `apple_calendars` (names; empty means all).
4. Tests use a fake store; CI's macOS job proves the import.

## Not verified here (Mac session)

The real permission prompt; whether access granted to the terminal also works when the launchd job
runs (macOS ties calendar permission to the app that asks); that `requestFullAccessToEventsWithCompletion_`
(macOS 14 and later) or the older call is the one that exists on the Mac; and that
`availability()`/`status()` values match the constants used. Record the result in this ADR.

## Consequences

The EventKit-facing code (`EventKitStore`, `open_event_kit_store`) was written without a Mac, tested
against fakes of the calls it makes.
