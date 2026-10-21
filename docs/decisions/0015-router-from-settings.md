# 0015. Building the router from settings

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-64, for epic CH-16 (step 2)
- Deciders: Sam (owner), Priya (reviewer), Nicholas (approver)

## Context

The router, cache, cost log, and providers exist (ADR-0003, 0005, 0006, 0007), but nothing built
them from the user's configuration. The PRD says the routing table is a config file and API keys
live in the Keychain.

## Decision

1. **`settings.json` gains `providers`** (name, kind, model, optional base URL, `trains_on_data`,
   per-million-token prices) **and `model_routes`** (task name to an ordered list of provider names:
   first is tried, the rest are escalations). Validation rejects duplicate provider names, empty
   routes, and routes naming unknown providers. There is no field for an API key.
2. **`build_router(settings, store, sessions)`** creates the adapters, the response cache, and a
   cost log with the monthly budget. A cloud provider whose key is missing is left out and reported
   in `problems`, so local models keep working; the key's value never appears in a message.
3. **`routes_for(settings, task, built)`** returns the task's providers in order, limited to those
   built, or raises `NoRoute`.
4. **`trains_on_data` defaults to False**; the user sets it per provider from their plan's terms.

## Evidence

`tests/llm/test_setup.py`: missing keys, route filtering, a router built from settings that still
refuses Sensitive data to the cloud, serves cache hits, and stops non-essential calls over budget
(all with a mock transport), and settings validation.

## Alternatives considered

- **Keys in settings.json.** A plain file is the wrong place for secrets (ADR-0001).
- **Fail when any key is missing.** Would stop local-only use and hide which providers work.

## Consequences

- Steps that call a model (`hub extract`, `hub score`) need only `build_router` and `routes_for`.
- The initial choice of models and routes is left to the user and the evals.
