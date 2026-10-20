# 0003. The router contract: tiers, providers, and a single choke point

- Status: Accepted
- Date: 2026-10-20
- Ticket: CH-48, for epic CH-16 (step 2)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

The PRD requires every model call to go through one router that enforces three privacy tiers in
code, with its own tests (Model routing and privacy tiers; Security > Model providers). The
router also has to take Anthropic, OpenAI, Google, and local models behind one interface, and
later add validation, caching, and cost tracking.

## Decision

1. **`PrivacyTier` is an ordered enum** (Public < Personal < Sensitive). Every `LLMRequest` must
   state its tier; the field has no default, so forgetting it is a `TypeError`, not a guess.
2. **A provider declares two facts**: its `destination` (local or cloud) and `trains_on_data`.
   These come from settings (PRD: settings record each provider's retention terms).
3. **One function, `check_allowed`, holds the rule**, and `Router.complete` calls it before any
   provider code runs. Sensitive goes to local providers only. Personal never goes to a cloud
   provider that trains on API data. Public goes anywhere.
4. **A refusal raises `PrivacyViolation` and never falls back** to another provider. Choosing
   another route is the caller's decision, made visibly.
5. **Providers are a `Protocol`** (structural typing), so the real adapters and the test fake
   need no shared base class. Providers do no privacy checks themselves.
6. **Task routing (which model for which task) is not in this ticket.** The PRD says the evals
   decide the routing table, so the router takes an explicit provider name for now.

## Evidence

`tests/llm/test_router.py`: a Sensitive call to a cloud provider is refused and the provider is
never called; local providers accept every tier; cloud providers accept Public and Personal;
a training provider is refused for Personal; a refusal doesn't reach a second provider.

## Alternatives considered

- **LiteLLM as the router.** The PRD allows it optionally. Its routing and fallback features would
  put the privacy rule outside our code and tests. It may sit *under* a provider adapter later.
- **Tier checked inside each provider.** Duplicates the rule per adapter; one forgotten check is a
  leak. A single choke point is testable once.
- **Defaulting an unspecified tier to Sensitive.** Safe, but hides missing tagging; a required
  field forces every call site to decide.

## Consequences

- Later features (validation, cache, cost) wrap `Router.complete` and must not bypass
  `check_allowed`; a cache hit for a Sensitive prompt is still only ever stored locally.
- Callers choose providers by name until the routing table exists.
- The tier of a call is only as accurate as its caller's tagging; the code that builds prompts
  must tag them, and reviewers should look for that.
