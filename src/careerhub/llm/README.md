# `careerhub.llm`

The model router. Every model call in Career Hub goes through it; nothing else may call a model.
See [ADR-0003](../../../docs/decisions/0003-router-contract.md).

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `types` | `LLMRequest`, `LLMResponse`, `PrivacyTier` | CH-48 | Built |
| `provider` | The `Provider` interface and `Destination` (local or cloud) | CH-48 | Built |
| `router` | Privacy check, then the call; raises `PrivacyViolation` | CH-48 | Built |
| `fake` | Scripted provider for tests | CH-48 | Built |
| `validated` | `complete_validated`: Pydantic-checked output, one retry, then escalate | CH-49 | Built |
| `cache` | Response cache in the encrypted database | CH-50 | Built |
| `cost` | Cost log and the monthly budget | CH-51 | Built |
| `setup` | `build_router(settings, store, sessions)` and `routes_for` | CH-64 | Built |
| `catalog` | Built-in presets, switched on by a key; default routes | CH-99 | Built |
| `commands` | `hub models list, add, remove, add-custom, routes` | CH-108 | Built |
| `providers` | Anthropic, OpenAI, Google, and local adapters | CH-52 | Built |

## The privacy rule

| Tier | Local provider | Cloud provider |
| --- | --- | --- |
| Public | yes | yes |
| Personal | yes | yes, unless the provider trains on API data |
| Sensitive | yes | **never** |

- Every `LLMRequest` must state its tier; there is no default.
- `Router.complete(request, provider_name)` checks the tier *before* the provider sees the
  request, and a refusal never falls back to another provider.
- Tests use `FakeProvider`. No test makes a network call.

## Validated output

`complete_validated(router, request, Schema, routes=["cheap", "strong"])` returns a `Schema`
object. Each provider gets one attempt plus one retry (the retry tells the model what was wrong,
without quoting its answer); then the next route is tried. Escalation goes through the router, so
a Sensitive call can never escalate to a cloud provider. If everything fails it raises
`ValidationFailed`, whose message lists only field names and rules, never the model's text.

## Response cache

`Router(providers, cache=ResponseCache(sessions))` answers a repeated call from the encrypted
database (`llm_cache` table). The key is provider, model, and a hash of the system text, prompt,
and token limit; the prompt itself is never stored. The privacy check runs *before* the lookup,
and a hit comes back with `cached=True` so the cost log (CH-51) can count it as free. There is no
expiry yet; a changed prompt or model is a different key. See
[ADR-0005](../../../docs/decisions/0005-response-cache.md).

## Cost and budget

`Router(providers, cache=..., costs=CostLog(sessions, budget, prices, run_id))` logs tokens and
estimated dollars for every call that reaches a provider (`llm_usage` table). When this month's
spend reaches `monthly_budget_usd` (a setting, default $20), non-essential cloud calls raise
`BudgetExceeded`; set `essential=True` on a request to keep it running. Local models and cache
hits are always free. See [ADR-0006](../../../docs/decisions/0006-cost-log-and-budget.md).

## Real providers

`AnthropicProvider`, `OpenAIProvider`, `GoogleProvider`, and `LocalProvider` (an OpenAI-style
server on localhost) each take a name and model; cloud ones also take an API key. Keys live in the
Keychain as `api-key-<provider name>` (for example `api-key-anthropic`); read one with
`load_api_key(store, name)`. HTTP errors say only the status code. Tests use a mock transport.
See [ADR-0007](../../../docs/decisions/0007-real-providers.md).

## Configuring models

In `settings.json` (data folder), list `providers` and map each task to an ordered list of them:

```json
{
  "monthly_budget_usd": 20,
  "providers": [
    {"name": "claude-cheap", "kind": "anthropic", "model": "<model id>",
     "input_per_mtok": 1.0, "output_per_mtok": 5.0},
    {"name": "local-llama", "kind": "local", "model": "llama3"}
  ],
  "model_routes": {"extract": ["claude-cheap", "local-llama"]}
}
```

Keys are never in this file: store each cloud provider's key in the Keychain as
`api-key-<provider name>`. `build_router(...)` leaves out cloud providers with no key and lists
them in `problems`. See [ADR-0015](../../../docs/decisions/0015-router-from-settings.md).

## Model catalog

`catalog.json` lists ready-made models (Claude Haiku and Sonnet, two OpenAI models, Gemini Flash
free and paid, and a local Ollama model). A preset is active when its API key is in the Keychain
(`api-key-<preset name>`), or for the local one when Ollama answers. `with_catalog` adds the active
presets and default routes for tasks you haven't routed: Public work goes to the cheapest model,
Personal work only to models that don't train on your data, and local is the last fallback. Your
own `providers` and `model_routes` always win; `preset_overrides` changes a preset's model, address,
or prices. Prices carry a checked-on date. See [ADR-0029](../../../docs/decisions/0029-model-catalog.md).

### `hub models`

```
hub models list                       presets, which are on, prices and the date checked
hub models add anthropic              asks for the key (hidden); one key switches on Haiku and Sonnet
hub models add gemini --tier free     the tier is required: free trains on your data
hub models remove openai              asks first, then deletes the key from the Keychain
hub models add-custom NAME KIND MODEL [--base-url U] [--input N] [--output N] [--trains-on-data]
hub models routes                     the model each task will try, and why
```

Keys go only into the Keychain: never printed, logged, or written to `settings.json`.
