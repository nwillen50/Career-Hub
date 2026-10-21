# 0024. Tailoring notes: advice only, minimal data, redone only when needed

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-75, part of CH-29 (step 8)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

PRD (Tailor): which experiences to lead with, which gaps to address, suggested resume changes, as
written notes. Generating LaTeX resume variants is Later.

## Decision

1. **Notes only.** Three short lists (lead with, gaps, resume edits), max five items each, validated
   with a Pydantic schema. Nothing edits the resume or writes or sends an application. The prompt
   (`tailor_job_v1.md`) says to use only what the resume shows and to name real gaps.
2. **Tier PERSONAL, not essential.** The resume goes only to providers allowed to see Personal
   data, and the monthly budget pauses tailoring before anything essential.
3. **Data minimization.** The LaTeX source is reduced to plain text, and email addresses, web
   addresses, and phone numbers (10+ digits) are replaced with `[email]`, `[link]`, `[phone]`
   before the model sees it. The profile block leaves out the name (the resume's own heading may
   still contain it; the model doesn't need contact details, and the name is on the resume
   employers see anyway, per PRD).
4. **Only scored (shortlisted) jobs** get notes; the nightly run tailors the shortlist's top 5 when
   a `tailor` route and a resume exist, and does nothing and says nothing when no route is set.
5. **Saved on `Assessment.tailoring`** with the prompt version, a hash of the redacted resume text,
   and the model; redone only when the prompt or the resume changes.
6. The shortlist page shows the notes in a collapsed "Tailoring notes" section (escaped).

## Evidence

`tests/modules/jobs/test_tailoring.py` and two cases in `tests/test_pipeline.py`: LaTeX to text,
contact redaction (and ordinary numbers left alone), request tier and contents, saving and skipping,
unscored jobs, training-provider refusal, schema limits with retry, page rendering.

## Alternatives considered

- **Send raw LaTeX.** Wastes tokens on markup and sends contact details.
- **Generate a revised resume.** Out of scope, and risks invented experience.

## Consequences

- Notes quality is unmeasured; the weekly spot check (PRD) covers it.
- The regex redaction is best effort; unusual contact formats could slip through.
