# 0017. Rubric scoring: the model rates, the code adds up

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-67, part of CH-26 (epic CH-25, step 6)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

Fit scores must follow the rubric file and store their explanation, model, and prompt version
(CH-26). The PRD calls this "Judgment" work for a mid-tier model that may see Personal data.

## Decision

1. **The model rates each criterion 0 to 10 with a reason, and writes a short explanation.** The
   **code** computes the fit score: a weighted average scaled to 0-100. Arithmetic is not left to
   the model. A missing, repeated, or unknown criterion raises `ScoringError`.
2. **Prompt `score_job_v1.md`** (versioned). It tells the model to give 5 and say so when the
   posting doesn't settle a criterion, rather than guessing high or low.
3. **Tier PERSONAL.** The prompt contains skills, interests, education, and the user's own "about"
   text, but **not their name** and not the resume text or transcript. So it can go to a cloud
   provider only if that provider doesn't train on API data (ADR-0003).
4. **Saved on `Assessment`:** `fit_score`, per-criterion `criteria_scores` (new column), the
   `explanation`, `model_used` (`provider:model`), `prompt_version`, `rubric_version`. A job already
   scored with the same prompt and rubric versions is skipped; a new rubric version re-scores it.
5. **Only jobs that passed the filters are scored.** `ranked_jobs` returns open, non-duplicate,
   filtered-in jobs by score. The PRD also ranks by odds and urgency; those are added when they
   exist, and the order is by fit alone until then.
6. `complete_validated_detailed` returns the response alongside the parsed object so the
   answering model can be recorded.

## Evidence

`tests/modules/jobs/test_scoring.py`: weighted math, bad criteria sets, provenance saved, skip and
re-score on rubric change, filtered jobs skipped, name left out, privacy refusal for a training
cloud provider, no row written on a bad answer, ranking order and exclusions.

## Alternatives considered

- **Model gives the overall score.** Less consistent run to run and not tied to the weights.
- **Send the resume text.** More signal, more personal data; profile fields suffice to start and
  the eval (CH-68) will say whether more is needed.

## Consequences

- Score quality is unmeasured until the labeled set exists (CH-7, CH-68).
- Explanations come from the model; the weekly spot check (PRD) is the guard.
