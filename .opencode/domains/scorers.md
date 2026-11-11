# Domain: scorers

Every external classifier, its credentials and the shared rubric.

## Owns
- `scorers.py` - explicit provider registry, credential and endpoint display.
- `rubric.py` - shared choice/score questions, pure rubric, strict response validation.
- `scorer_http.py` - shared transport: no-redirect opener, reason ladder, response close, 30 s key cache.
- `scorer_retry.py` - one bounded retry of a transient status inside the same deadline.
- Adapters: `jev_client.py`, `openai_decision_client.py`, `openrouter_client.py`,
  `cloudflare_client.py`, `nace_client.py`, `custom_client.py`.
- `model_profiles.py` + `model_profiles.json` - local, exact-ID reference data only.

## Invariants
- Exactly two classification contracts: named choice from the route's allowed levels, or the shared
  numeric score `0..2` -> `low (<0.5)` / `medium (<1.5)` / `high`. Refusal, malformed or out-of-list
  values are rejected and fail open. An answer that was received is never retried.
- Transient HTTP 408/429/500/502/503/504/529 only: one retry, at least 300 ms must remain, `Retry-After`
  honoured only if it fits, 100 ms otherwise. Timeouts and transport errors are never retried.
- No provider falls back to Jev. Missing credential/model -> `model_missing` / `credential_required`.
- Keys resolve through `agent.secret_scope` then env; `credential_present()` never returns a secret.
- Prompts are shared only when a routing mode is active; see AGENTS.md invariant 8 before touching
  what is sent (`prompt_chars`, dialogue context, guidance, target-model context).
- Model profiles neither verify transport support nor change clamping.
- Route-specific injection exists and is deliberately narrow: top-level `verbosity` for exact
  OpenRouter Claude entries only, Gemini `thinking_level` for the exact registered native routes, and
  the exact DirectSDK Claude routes (`effort.DIRECTSDK_CHOICE_ROUTES`, existing
  `extra_body.reasoning.effort` only, never dynamic or trialled). Do not widen any of them.

## Known traps
- Do not add a remapping for a historical route (Ox Alpha `medium` -> 400 is history, not a warning).
- Request bodies must stay byte-identical when the new setting is off; there is a fixture
  (`tests/fixtures/scorer_request_bodies.json`) that compares them.
- `custom` with `chat_completions` fails open as `unsupported_api_format`, on purpose.

## Proof
- `.\scripts\run_tests.ps1 -q -k "scorer or client or rubric"`, plus the adapter's own module.
- `.\scripts\run_lint.ps1`.
- A live call cannot be proven offline: say so in `gaps:` and never fake a provider response.

## Open threads
- No live call has exercised OpenRouter's Decisions request schema.
- ZDR is only asserted for OpenRouter; Jev, Cloudflare, Nace and custom endpoints are unverified.
