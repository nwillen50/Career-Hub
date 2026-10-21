# 0027. The weekly plan

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-77, part of CH-29 (step 8)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

PRD (Plan): build a weekly plan from the time budget, deadlines, and recruiting timelines; putting
blocks on the calendar after approval is Later. Weekly time budget is in `preferences.yaml`.

## Decision

1. **Pure code, suggestions only.** `plan_week(tasks, free, budget, zone)` places tasks in free
   gaps. Nothing is written to a calendar, saved, or sent. `hub plan` prints the suggestion.
2. **Tasks from the shortlist:** "Apply" (90 min) for each of the top jobs, plus "Message <person>"
   (30 min) when a warm path has any strength, due a day before the application deadline so a
   referral can still help. Deadlines come from extracted fields.
3. **Order:** soonest deadline, then highest fit score. Each task takes the earliest gap that fits
   and never ends after its deadline.
4. **Limits:** total planned time never exceeds the weekly budget; no day gets more than half of it
   (spread the work); working hours default to 09:00-21:00 local, from now on, for 7 days, minus
   calendar busy time.
5. **Nothing is dropped silently:** tasks that don't fit are listed with the reason (budget used up,
   no gap long enough, nothing before the deadline, remaining days already full).
6. No `Plan` table yet: the plan is computed on demand. Tracking completion (plan vs. actual) comes
   with the Tracker.

## Evidence

`tests/modules/plan/test_weekly.py`: ordering, budget as a hard limit, daily share, deadlines, gap
length, no overlaps and all blocks inside free time, task building (outreach, deadlines, bad
dates), whole-week planning around a calendar file, and the command.

## Alternatives considered

- **A model to plan.** Scheduling is arithmetic; code is predictable and free.
- **Optimal packing.** First-fit by urgency is easy to explain; revisit if plans look wasteful.

## Consequences

- Durations (90 and 30 minutes) and the 09:00-21:00 window are defaults to tune from real use.
- Follow-ups and networking tasks beyond the shortlist wait for the Tracker's data.
