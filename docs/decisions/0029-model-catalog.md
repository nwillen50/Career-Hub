# 0029. Built-in model catalog and default routes

- Status: Accepted (preset choices flagged for Nicholas)
- Date: 2026-10-22
- Ticket: CH-99, for epic CH-16 (step 2)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

CH-64 left the app with no providers and no routes: the user had to hand-write both. Nicholas asked
for a basic set of models that switch on by adding a key.

## Decision

1. **`llm/catalog.json`** (data, not code) lists presets: kind, model id, strength, prices per
   million tokens, `trains_on_data`, plus one `prices_checked` date. Gemini has two presets, `gemini-free`
   (trains on data, so Public only) and `gemini-paid`; the tier is never assumed.
2. **A preset is active** when its Keychain key exists (key name `api-key-<preset name>`, as for any
   provider); the local preset when Ollama answers on localhost.
3. **`with_catalog(settings, store)`** adds active presets as providers and fills in default routes
   for tasks the user hasn't routed. Public tasks (`extract`): cheapest first, local last. Personal
   tasks (`score`, `tailor`, `profile_extract`): only providers that don't train on data, cheapest
   then stronger, local last. No eligible provider means no route, and the reason says so.
4. **The user always wins**: their `providers`, `model_routes`, and a same-named provider take
   precedence. `preset_overrides` can change a preset's model, address, or prices, but not
   `trains_on_data`. Default routes use presets only, so a hand-configured provider is never
   routed silently.
5. The router's privacy check is unchanged and still refuses Personal data to Gemini free.

## Evidence

`tests/llm/test_catalog.py` covers catalog validation, route combinations (Gemini free only; free +
Haiku + Sonnet; local only; mixed), user precedence, overrides, and stale prices.

## Consequences

Not built here (follow-up ticket): the `hub models` commands that store keys. Prices and ids for
OpenAI and Gemini were taken from third-party trackers on 2026-10-22 because the vendors' pages
were unreachable from the build environment, so they need a check against the official pages.
