# 0031. Reading the resume and transcript into reviewable facts

- Status: Accepted (default route flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-91, for epic CH-18 (step 3)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Career Hub held the resume and transcript files but didn't understand them, so scoring saw only the
short About text. The PRD says anything inferred is a proposal the user accepts.

## Decision

1. **Task `profile_extract`**, Personal tier, not `essential` (the budget pauses it first). The
   text is redacted first: `personal_text` for a resume, `transcript_text` (also IDs, birth dates,
   SSN-shaped numbers, addresses) for a transcript.
2. **`ProfileFacts`** schema (experiences, education, courses with grade and term, GPA, skills,
   projects, awards) with length and count limits; answers go through `complete_validated`.
3. **Stored as a pending proposal** in `profile_extraction` with the file's SHA-256 and prompt
   version; the same file and prompt are never read twice. Review, acceptance, and use in scoring
   are CH-110. Facts typed in `profile.md` will always win.
4. **Default route**: with the model catalog, `profile_extract` goes to the cheapest provider that
   doesn't train on data, then local (ADR-0029). The recommended setup is Haiku, then local
   (a few cents per resume). The nightly run does this step only if a route exists.
5. The nightly run reads at most the current resume and transcript; it does not delete older rows.

## Evidence

`tests/profile/test_profile_extract.py` (pending rows and hashes, no rereads, no name/contacts/IDs
in prompts, invalid answers, unreadable files, training providers refused, hostile text, size
limits) and a nightly test.

## Consequences

Older pending or rejected rows stay in the table until CH-110 decides how they are cleaned up.
