# 0044. Skills are a light, partial signal; pay is scored

- Status: Accepted (defaults confirmed by Nicholas on Oct 23)
- Date: 2026-10-24
- Ticket: CH-175, for epic CH-25 (step 6). Amends 0037 (CH-102)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

A profile's skills list is never complete: Nicholas lists Python but can also code in R, and soft
skills are harder still. Field, location, level, and pay should drive recommendations much more.

## Decision

1. Scoring prompt version 3 says the skills list is a partial sample. A job skill the person did
   not list is not a gap when education, experience, or a related skill makes it likely. Soft
   skills are judged from experience. Explanations do not cite "you didn't list X" unless X is a
   stated hard requirement with nothing related in the profile.
2. The starter rubric gains **pay** (weight 2), scored against preferences when the posting states
   pay and neutral (5) when it does not. No skills criterion. An existing `rubric.yaml` is not
   changed; the user can add pay through a rubric proposal.
3. The readiness gate changes: `profile_min_skills` defaults to 1 (was 5). 0 still turns it off.
   The message says scoring uses skills as a rough guide.
4. A paired eval (`evals/ranking/pairs.yaml`, `rank_pairs.py`) checks that an unlisted but related
   skill (R for Python) scores within 5 points, and that a pay mismatch scores lower.

## Consequences

Bumping the prompt version means the next nightly run scores every job again (one-time model
cost, inside the monthly budget guard). The pair eval needs real models to mean anything, so it
runs by hand after a prompt change; the tests use a scripted model to prove the harness.
