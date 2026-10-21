# 0012. Source approval

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-61, part of CH-22 (epic CH-21, step 4)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

The PRD: sources may be proposed (by the user, later by the agent), but "No source is added
without the user's approval, and a test proves it."

## Decision

1. **Every source starts `proposed`.** `propose_source` never approves; proposing a URL that
   already exists returns the saved row unchanged (it can't reset an approval).
2. **Only `approve_source` makes a source `approved`**, and it is called only from a user action
   (`hub sources approve <id>` now; the web UI later). Agent code must not import it.
3. **Fetching code calls `require_approved(source)` first**; proposed, rejected, and disabled
   sources raise `SourceNotApproved`. `approved_sources` lists what may be fetched.
4. **`hub sources add` saves a source as proposed** even when the user types it in, so there is
   one path and one record of the decision (`decided_at`).

## Evidence

`tests/modules/jobs/test_sources.py`: proposed sources are unusable until approved; rejected and
disabled stay unusable; re-proposing changes nothing; the command works end to end.

## Alternatives considered

- **Auto-approve sources the user types in.** Two paths to test and an easy bypass; one extra
  command is cheap.
- **Use the `Proposal` table for sources.** It models suggestions of changes to settings; a
  source already carries its own status, so a second record adds nothing yet.

## Consequences

- The separation between "agent proposes" and "user approves" is by convention plus review of
  imports; a stronger check (a lint rule) could come with source discovery.
