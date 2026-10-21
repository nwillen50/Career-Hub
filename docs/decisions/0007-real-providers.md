# 0007. Real providers: thin httpx adapters

- Status: Accepted
- Date: 2026-10-21
- Ticket: CH-52, for epic CH-16 (step 2)
- Deciders: Sam (owner), Leo (reviewer), Nicholas (approver)

## Context

The router (ADR-0003) needs real adapters behind the `Provider` protocol for Anthropic, OpenAI,
Google, and local models. The PRD asks for "a thin in-house adapter, optionally on top of LiteLLM".

## Decision

1. **Direct HTTPS with `httpx`**, one small class per API, instead of each vendor's SDK or LiteLLM:
   one new dependency, and no hidden retries, fallbacks, or logging between the router and the wire.
2. **Local = any OpenAI-compatible server on this machine** (Ollama, LM Studio, llama.cpp).
   `LocalProvider` refuses a `base_url` whose host isn't localhost, so a "local" provider can't
   be misconfigured into sending Sensitive data off the Mac.
3. **API keys are passed in by the caller** and read from the Keychain with `load_api_key`
   (entry `api-key-<provider name>`). Keys go in headers, never URLs.
4. **Errors carry the status code only** (`ProviderError`): a response body can echo the prompt.
5. **`trains_on_data` is a constructor argument, default False**, to be set from settings per
   provider terms (PRD). Anthropic, OpenAI, and Google API terms don't train on API data by default,
   but Nicholas should confirm for the plans he uses.
6. **Tests use `httpx.MockTransport`**; nothing here touches the network.

## Evidence

`tests/llm/test_providers.py`: request shape and reply parsing for each API, key placement,
local-host check, Sensitive calls refused for cloud and allowed for local, safe errors.

## Alternatives considered

- **Vendor SDKs.** Three heavy dependencies with their own retry and logging behavior.
- **LiteLLM.** Optional in the PRD; its routing would sit around our privacy check.

## Consequences

- Model names and prices are configuration, passed in by the caller; none are hard-coded here.
- Wiring providers from settings (names, models, prices) comes with the first step that calls a
  model for real (step 5).
- Streaming, tool use, and images are not supported; the pipeline needs only text in and out.
