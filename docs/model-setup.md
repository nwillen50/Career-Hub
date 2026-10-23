# Recommended model setup

The setup Nicholas chose on Oct 21: a free Gemini key for reading public postings, Claude Haiku for
the work that sees your profile, Sonnet only as an escalation, and a local model as the fallback.
Expected cost: a few dollars a month at most (tailoring is about 15 cents).

| Task | Sees | Models, in order |
| --- | --- | --- |
| `extract` (postings into fields) | Public data | `gemini-free`, then `local-8b` |
| `score` (rubric ratings) | Your profile | `claude-haiku`, then `claude-sonnet` |
| `tailor` (resume notes) | Your profile and resume | `claude-haiku`, then `local-8b` |
| `profile_extract` | Your resume and transcript | `claude-haiku`, then `local-8b` |
| `chat` (questions in the conversation panel) | Your saved scores and explanations | `claude-haiku`, then `local-8b` |

Gemini's free tier may train on what you send, so it only ever gets postings. The router refuses to
send anything personal to it, and the settings below never route it there.

## Steps

1. Switch the models on (each asks for the key, hidden, and stores it in the Keychain):
   ```bash
   uv run hub models add anthropic          # one key switches on Haiku and Sonnet
   uv run hub models add gemini --tier free
   uv run hub models add local              # prints the Ollama steps
   ```
2. Copy [`examples/settings.recommended.json`](examples/settings.recommended.json) to
   `~/Library/Application Support/CareerHub/settings.json` (or merge it into yours). It sets the
   routes above and a **$5 monthly budget**.
3. Install Ollama (ollama.com), then `ollama pull llama3.1:8b` and keep it running. A 4-bit 8B model
   uses about 5 GB, which fits the 16 GB MacBook Air M2 alongside normal use. The Air has no fan, so
   a long run slows down instead of getting loud; that is fine overnight.
4. Check: `uv run hub models routes` shows what each task will try, and `uv run hub models list`
   shows prices and when they were last checked.
5. In the Anthropic Console, set a **monthly spend limit** as a hard backstop beyond the app's
   budget.
6. The Mac must be plugged in and awake for the nightly run. The wake schedule (`pmset`) is part of
   the scheduling work (CH-34).

## When something is unavailable

- Gemini's key is missing, or its free-tier rate limit is hit: extraction falls through to the
  local model, and the nightly report lists "fell back past a model (gemini-free: ProviderError, Nx)".
- Haiku is paused by the monthly budget: tailoring and profile extraction use the local model;
  scoring is essential and keeps running up to the budget ceiling in ADR-0020.
- The local model is not running: the task reports a problem and is skipped; nothing crashes.

Model ids and prices live in `src/careerhub/llm/catalog.json` with the date they were checked;
override any of them with `preset_overrides` in settings.
