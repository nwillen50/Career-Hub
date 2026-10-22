# 0032. Accepting extracted facts and using them in scoring and tailoring

- Status: Accepted
- Date: 2026-10-22
- Ticket: CH-110, for epic CH-18 (step 3)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

CH-91 saves facts read from the resume and transcript as pending proposals. The PRD says inferred
facts count only after the user accepts them, and that what the user typed wins.

## Decision

1. `hub profile facts` lists pending sets; `accept <n|all>` and `reject <n>` decide them. Only
   accepted sets are used. `hub profile show` reports counts, not facts.
2. The newest accepted set per document kind (resume, transcript) is used; older accepted sets are
   ignored, not deleted.
3. `profile_text(doc, facts)` appends the accepted facts (through `personal_text`, so they are
   redacted too). `profile.md` wins: skills already typed are not repeated, and extracted education
   for a school the user listed is dropped. Scoring's input hash and tailoring's hash include the
   facts, so accepting new facts re-scores and re-tailors once.
4. `profile/evals.py` and `evals/profile/sample.yaml` (made-up documents) measure extraction by the
   share of expected items found per field.

## Evidence

`tests/profile/test_facts.py`.

## Consequences

Accepting facts triggers a one-time re-score of the shortlist (a model cost); rejected and
superseded rows stay in the table.
