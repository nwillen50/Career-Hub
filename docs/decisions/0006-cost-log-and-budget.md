# 0006. Cost log and monthly budget

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-51, for epic CH-16 (step 2)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

The PRD: "cost is logged per run, with a monthly budget that pauses non-essential calls when
reached", and model spending for a month must stay within the user's budget setting.

## Decision

1. **`llm_usage` table:** one row per call that reached a provider (provider, model, tokens,
   estimated dollars, optional `run_id`). No prompt or response text. Cache hits are not logged.
2. **Prices come from a table the caller passes** (`Price` per provider name, dollars per million
   tokens). Not hard-coded, because prices change. Local models cost $0; an unpriced cloud
   provider also logs $0 (wired up with the real providers, CH-52).
3. **Budget is a setting**, `monthly_budget_usd`, default **$20**. "Month" is the UTC calendar month.
4. **`LLMRequest.essential`** (default `False`). Over budget, the router raises `BudgetExceeded`
   for non-essential *cloud* calls. Essential calls, local calls, and cache hits always proceed.
5. **Order in the router:** privacy check, cache, budget, provider call, log.
6. A `CostLog` with a `run_id` also adds each call's cost to `run_log.cost_usd`.

## Evidence

`tests/llm/test_cost.py`: cost from tokens, local free, budget pause, essential/local/cache
exceptions, month boundary, run cost.

## Alternatives considered

- **Hard stop for everything.** Would stall the nightly shortlist; essential calls are the
  PRD's own wording.
- **Prices in settings.json.** Reasonable later; deferred until real providers exist.

## Consequences

- Callers must mark the calls the shortlist can't do without as `essential=True`.
- The $20 default and the "non-essential by default" choice are guesses for Nicholas to change.
