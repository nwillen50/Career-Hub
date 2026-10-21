# 0019. The shortlist page: static, escaped, no scripts

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-69, part of CH-27 (epic CH-25, step 6)
- Deciders: Leo (owner), Sam (reviewer), Nicholas (approver)

## Context

Milestone A is "a ranked, explained shortlist written each night to a local HTML page the user
bookmarks, built from the templates Today will reuse". The page shows text that came from the
internet (postings), so it is an injection surface.

## Decision

1. **One self-contained HTML file**, no scripts, no external requests, written to
   `shortlist/index.html` in the data folder with mode 600. It is replaced atomically (write a
   temporary file, then rename), so a reader never sees a half-written page.
2. **Everything from a posting is HTML-escaped**, and a job's link is clickable only when it is
   `http` or `https` with a host; `javascript:`, `data:`, `file:`, and scheme-less links render as
   plain text. Link attributes are escaped, and links use `rel="noopener noreferrer"`.
3. **Content per the PRD's Today:** at most 5 ranked jobs, each with title, company, location, fit
   score, a one-line reason (first sentence of the explanation), and an expandable full
   explanation with the per-criterion table; when the last *successful* run finished.
4. **States:** an empty state that says what to check, and an error state (`role="alert"`) when
   the latest nightly run failed, showing only the error's type name, never its message.
5. **Accessibility and design:** real headings, landmarks, and a table with a caption and header
   cells; visible focus; system light or dark mode via `prefers-color-scheme`; readable at phone
   width. Colors are defined as variables once.
6. **Plain string rendering with `html.escape`**, no template engine dependency for one page; the
   web app (step 9) may adopt a template engine and reuse these pieces.
7. `hub shortlist build` writes the page now; the nightly run (CH-70) calls the same function.

## Evidence

`tests/test_shortlist.py`: content, hostile titles and companies, hostile links and attribute
breakouts, empty and error states, no scripts or external requests, dark mode, top-5 order, run
status, other steps ignored, file mode and atomic replace, and the command.

## Alternatives considered

- **Jinja2 templates.** Autoescape would help, but it is a new dependency for a single page; the
  escaping here is explicit and tested.
- **A script for expand/collapse.** `<details>` does it without code.

## Consequences

- Exploration picks, contacts, follow-ups, deadlines, and weekly-plan progress (the rest of Today)
  arrive with their steps.
- The page contains personal context (explanations mention the user's profile), so it stays in the
  private data folder and is never uploaded.
