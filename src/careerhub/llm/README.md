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
| `cache` | Response cache in the encrypted database | CH-50 | Planned |
| cost log, budget | Per-run cost and the monthly budget | CH-51 | Planned |
| real providers | Anthropic, OpenAI, Google, local | CH-52 | Planned |

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
