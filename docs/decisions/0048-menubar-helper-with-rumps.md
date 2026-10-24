# 0048. The menu-bar helper is a small Python program using rumps

- Status: Accepted (to be confirmed in a Mac session; CH-33 still tests notifications)
- Date: 2026-10-24
- Ticket: CH-191
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

The PRD wants a menu-bar icon with a count, an error mark, and one item, "Open Today". The counts
already exist (`hub attention --json`, CH-146). CH-191 said to pick between a Python menu-bar
library and a tiny Swift app.

## Decision

1. **Python with `rumps`** (MIT, a thin wrapper over AppKit), installed only on request
   (`uv sync --extra menubar`, macOS only), so Linux and CI never need it. One language, one
   test setup, no second build to maintain or sign.
2. The helper **never opens the database**. Every minute it runs `hub attention --json` as a
   separate process and shows the label and tooltip, so the Keychain prompts and the database key
   stay in the one program that already handles them, and the helper sees counts only.
3. "Open Today" starts `hub web` if nothing listens, reads the one-time link it prints (accepted
   only if it is `http://127.0.0.1:<port>/?t=...`), and opens it. If a terminal already runs
   `hub web`, the helper says to use that window's link rather than guess its secret.
4. It starts at login through a launchd agent (`com.careerhub.menubar`, `RunAtLoad`, restarted only
   after a crash) that `hub schedule install` writes when `menubar_on` is set. Off until asked, and
   never loaded for the user.

## Not decided here

Whether a Python process can post macOS notifications without a signed app bundle is the CH-33
spike; the morning notification keeps working through `osascript` meanwhile (CH-147). If rumps
turns out unreliable in the Mac session, a Swift app would read the same JSON.

## Consequences

The icon, the login agent, and "Open Today" need a real Mac to verify (steps in
`docs/mac-session.md`). Everything else is tested without one.
