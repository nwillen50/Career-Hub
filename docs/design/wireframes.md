# Wireframes: Today, Tracker, Job detail, Conversation panel, System status

- Ticket: CH-32 (owner: Leo). **For Nicholas's review before any interface code** (Leo's sign-off
  condition in the PRD). Nothing here is built; the shortlist page (CH-69) already uses the card
  layout shown for Today.
- Source: PRD "User experience". These are plain-text sketches: boxes are regions, not pixels.
- Questions for you are marked **Q**. Please answer in the PR; the answers become tickets.

## Principles

1. **Ten minutes a day.** Today shows a fixed, small amount: at most 5 ranked jobs, 2 exploration
   picks, 5 follow-ups. Everything else is one click away.
2. **Say why.** Every ranked job shows a one-line reason; the full explanation is one click away.
3. **You decide.** Career Hub proposes (Accept / Reject); it never sends, submits, or applies.
4. **Never silent.** Every screen has designed empty, loading, and error states, and says when the
   last successful run finished and what failed.
5. **Keyboard first for the daily loop:** `j`/`k` move, `i` interested, `x` not interested, `a`
   applied, `?` shows the shortcuts.
6. **Accessible:** WCAG AA contrast, real headings and landmarks for VoiceOver, visible focus,
   follows the system light or dark mode, works at a narrow window.

## Layout shared by every screen

```
+--------------------------------------------------------------------------------+
| Career Hub    [Today] [Tracker] [System]                    last run: 6:02 am ok |
+--------------------------------------------------------------------------------+
| (page content)                                                                 |
+--------------------------------------------------------------------------------+
```

The top bar shows the last successful run on every page. A failed run turns it into a red
"Last run failed: see System" link.

## 1. Today

```
+--------------------------------------------------------------------------------+
| Today, Wed Oct 21                                  [Conversation >]            |
+--------------------------------------------------------------------------------+
| Top jobs (5)                                                                   |
| +----------------------------------------------------------------------------+ |
| | 1. ML Engineer, New Grad - Northwind Labs - Boston, MA            82 / 100 | |
| |    Strong match on field and level; Boston is on your list.                | |
| |    Contact: Ada Sample (alumni: Example University; you noted: mentor)     | |
| |    [i] Interested   [x] Not interested v   [a] Applied   [Open >]          | |
| +----------------------------------------------------------------------------+ |
| | 2. ...                                                                     | |
| +----------------------------------------------------------------------------+ |
| Exploration pick (1)                                                           |
| | A role slightly outside your usual fit, with the reason it was picked      | |
|                                                                                |
| Follow-ups due (3)                                          +4 more overdue >  |
| | Message Ben Example about Globex - due today              [Done] [Snooze]   | |
|                                                                                |
| Deadlines this week                                                            |
| | Fri Oct 23  Apply: Data Analyst at Contoso                                  | |
|                                                                                |
| This week's plan                              2h 30m of 5h planned, 1 of 4 done |
| | Wed 9:00-10:30  Apply: ML Engineer at Northwind Labs      [ ] done          | |
+--------------------------------------------------------------------------------+
```

- "Not interested v" opens a one-click reason: level, location, field, company, pay. Reactions
  become candidate labels for the evals.
- Empty state: "Nothing ranked yet. Check that a source is approved and the nightly run finished"
  with a link to System status.
- Error state: a banner at the top ("The last nightly run failed: <what>. These jobs are from an
  earlier run.") like the current shortlist page.

**Q1.** Should "exploration pick" be one or two by default (PRD says one, up to two)?
**Q2.** Is a one-line reason enough on the card, or do you want the top two criteria shown too?

## 2. Job detail

```
+--------------------------------------------------------------------------------+
| < Today                                                    [Conversation >]    |
| ML Engineer, New Grad - Northwind Labs - Boston, MA              82 / 100      |
| [Open the posting >]   status: new   first seen Oct 19                         |
+--------------------------------------------------------------------------------+
| Why it fits                                                                    |
|   Strong match on field. Level fits too.                                       |
|   Criterion          Score   Why                                               |
|   field              9/10    Right field                                       |
|   level              8/10    New grad program                                  |
+--------------------------------------------------------------------------------+
| Who to contact                                                                 |
|   Ada Sample - Engineer: you noted: mentor; alumni: Example University         |
+--------------------------------------------------------------------------------+
| Tailoring notes                                                                |
|   Lead with: ...   Gaps to address: ...   Resume edits to consider: ...        |
+--------------------------------------------------------------------------------+
| The posting (as the company wrote it)                                          |
|   ...text...                                          [Show original >]        |
+--------------------------------------------------------------------------------+
| [i] Interested   [x] Not interested v   [a] Applied   Follow-up date: [ ]      |
+--------------------------------------------------------------------------------+
```

Posting text is untrusted: always shown escaped as plain text.

**Q3.** Do you want the posting text collapsed by default (as drawn) or open?

## 3. Tracker

```
+--------------------------------------------------------------------------------+
| Tracker      View: [All v]  Follow-ups due | Applied, no reply 10+ days | ...   |
|              Filter: [__________]                          [Export CSV]        |
+--------------------------------------------------------------------------------+
| Company  | Role           | Status v     | Applied  | Contact | Follow-up | Next |
|----------+----------------+--------------+----------+---------+-----------+------|
| Northwind| ML Engineer    | applied    v | Oct 21   | Ada S.  | Oct 31    | ...  |
| Contoso  | Data Analyst   | interested v |          |         |           |      |
+--------------------------------------------------------------------------------+
| Tabs: [Applications] [Contacts] [Interactions]                                 |
+--------------------------------------------------------------------------------+
```

Sort by any column, edit cells in place, status from a dropdown, follow-up dates with a date
picker. The Contacts tab lists people (with how you know them, editable); Interactions is the log.
Empty state: "No applications yet. Mark a job Applied on Today to start tracking."

**Q4.** Statuses: interested, applied, interviewing, offer, rejected, withdrawn. Anything to add?
**Q5.** Which saved views do you want besides "Follow-ups due" and "Applied, no reply in 10+ days"?

## 4. Conversation panel (side panel on Today and Job detail)

```
+------------------------------+
| Conversation              [x]|
|------------------------------|
| You: why is #1 ranked first? |
| Career Hub: It scores 9/10   |
| on field because ...         |
|                              |
| You: I don't want sales roles|
| Career Hub proposes:         |
|  +------------------------+  |
|  | Add "sales" to excluded|  |
|  | title words            |  |
|  | - excluded_title: []   |  |
|  | + excluded_title:      |  |
|  |     [sales]            |  |
|  | [Accept]   [Reject]    |  |
|  +------------------------+  |
| [ type a message...      ]   |
+------------------------------+
```

Every change arrives as a proposal with a readable diff; nothing changes until you press Accept
(and an accepted rubric change bumps its version and runs the ranking eval). The panel sees at most
Personal-tier data and can only create proposals.

**Q6.** Where should rejected proposals go: gone, or a "Declined" list you can reopen?

## 5. System status

```
+--------------------------------------------------------------------------------+
| System                                                                         |
| Last run: Wed 6:02 am - succeeded - 2 problems (see below)                     |
| Sources:  Acme (greenhouse)  ok, fetched 6:01     Globex  FAILING since Mon    |
| Models:   this month $3.20 of $20 budget  [########----------]                 |
| Problems from the last run:                                                    |
|   - source 2 failed: FetchError                                                |
|   - 3 jobs could not be understood or scored                                   |
| Recent runs: Wed ok | Tue ok | Mon ok (backup ok)                              |
+--------------------------------------------------------------------------------+
```

Shows exactly the lines the nightly run already records (type names and counts only, never posting
or profile text).

## Menu-bar icon and the morning notification

- Icon: a count of pending actions (follow-ups due, new top jobs not yet reacted to). Error state
  (red dot) when the last run failed. Menu: "Open Today". There is no "Run now" in the menu (runs are
  scheduled; opening Today is the daily action).
- One notification each morning when the digest is ready; clicking it opens Today.
- Email to self is off by default and only ever to your own address.

**Q7.** Should the icon count only follow-ups due, or follow-ups plus unread top jobs?

## What I will not build until you answer

Interface code (CH-31): the local web app, menu-bar helper, and notifications. When you reply,
I'll turn the answers into tickets in the order Today, Job detail, Tracker, Conversation panel,
System status.

## Answers and changes (Nicholas, Oct 21)

These override the sketches above where they differ. The visual design (look, type, color,
navigation feel) is still being decided (CH-105); build structure and behavior with neutral styling
in design tokens until then.

**Q1.** One exploration pick by default; a setting can raise it to two.

**Q2.** The card's headline is the job title, company, location, salary, and overall score. If it
doesn't fit at the standard width, salary is always the part dropped (never location), and it moves
to the expandable section. Below the headline, an expandable section, closed by default, holds the
reason, the rubric breakdown, a short summary of the job, and who to contact. The full posting
stays on Job detail.

```
+----------------------------------------------------------------------------------+
| ML Engineer, New Grad · Northwind Labs · Boston, MA · $95k-$110k            82   |
|   [i] Interested   [x] Not interested v   [a] Applied   [Open >]   [More v]      |
|   (More, closed by default:)                                                     |
|   Why: strong match on field and level; Boston is on your list.                  |
|   The job: computer vision for warehouse robots; builds and tests models.        |
|   field 9/10 · level 8/10 · location 10/10 · ...                                 |
|   Contact: Ada Sample (Same school; you noted: mentor)                           |
+----------------------------------------------------------------------------------+
```

**Q3.** Posting text collapsed by default on Job detail.

**Q4.** Statuses: interested, reached out or referred, applied, interviewing, offer, rejected,
withdrawn, ghosted (set automatically after a period with no reply; a setting).

**Q5.** Saved views: Follow-ups due; Applied, no reply in 10+ days; Interviewing; Deadlines in the
next 7 days; Interested but not applied for over a week; Has a warm path; Interested. Marking a
job Interested is one click or the `i` key on Today, Job detail, and Tracker.

**Q6.** Declined proposals go to a Declined list you can reopen. Items leave the list after 30
days; Career Hub doesn't propose the same change again for 90 days. Both are settings.

**Q7.** The menu-bar count is follow-ups due plus new top jobs not yet reacted to. Any reaction
clears a job from the count.

**Further changes from the review:**

- **Plan tab.** "This week's plan" moves out of Today to its own tab: the top bar is Today, Plan,
  Tracker, System.
- **One section at a time on Today.** A row of chips at the top: All, Top jobs, Exploration,
  Follow-ups, Deadlines (keys 0 to 4). The choice is remembered between visits.
- **Job summary.** Job detail opens with a short plain-English summary of what the job really is
  (titles like "ML Engineer" are broad) and a role label such as "applied ML, computer vision".
  Extraction writes it from the posting alone (CH-98).
- **Tracker filters.** Filter chips by status (combine them, e.g. Interested + Applied) next to
  the text filter. Saved views are stored filter combinations.
- **Adjustable defaults.** Plan defaults (task lengths, working hours per weekday) are settings
  (CH-104).
