# 0005. Response cache: keyed by call, checked after the privacy rule

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-50, for epic CH-16 (step 2)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

Reruns of the nightly pipeline repeat many identical model calls. The PRD wants reruns to cost
nothing. A cache must not weaken the privacy tiers (ADR-0003).

## Decision

1. **Key:** provider name, model, and a SHA-256 of the system text, prompt, and `max_tokens`
   (length-prefixed so parts can't blur together). One unique row per key in `llm_cache`.
2. **Only the hash is stored**, not the prompt. The response text is stored in the encrypted
   database, like all personal data.
3. **The tier check runs first.** `Router.complete` calls `check_allowed`, then looks in the
   cache. The tier is not in the key, because the provider is: a Sensitive answer cached from a
   local model is never served under a cloud provider's name.
4. **Hits are marked** `LLMResponse.cached = True` so CH-51 can count them as zero cost.
5. **No expiry yet.** A new model name or prompt is a new key; manual clearing and expiry can be
   added if stale answers become a problem.

## Evidence

`tests/llm/test_cache.py`: repeat call skips the provider; different prompt, limit, or provider
misses; privacy refusal precedes the cache; prompt text absent from the row; no duplicate rows.

## Alternatives considered

- **Cache outside the router, in each caller.** Easy to forget; one place is testable.
- **Store the prompt for debugging.** Puts personal text in a second table for no user benefit.

## Consequences

- `complete_validated` goes through the router, so a cached *invalid* answer is replayed; its
  retry prompt differs, so the retry still reaches the model. Fine for now.
- Adding a `cache=` argument is the only change callers make.
