# OpenAI Decisions API integration

Implemented design record. The adapter is part of source commit `e1d7e99`, confirmed on
GitHub on 2026-10-07. Use [Configuration](../CONFIGURATION.md#openai-decisions) and
[Contracts](../CONTRACTS.md#scorer-adapter-contracts) for current setup and behavior.

The plan, implementation notes and test results below describe the 2026-10-06 work.
They are historical evidence, not a fresh deployment or live scorer check.

## Goal and verified contract

Add OpenAI Decisions as an explicitly selected scorer in `hermes-adaptive-effort`.
Its numeric result will enter the existing score, label, route clamp, and request
rewrite pipeline through `classify_detail(prompt) -> (score, failure)`.

OpenAI released Decisions in public beta on October 6. The documented endpoint is
`POST https://api.openai.com/v1/decisions`, with Bearer `OPENAI_API_KEY`
authentication. The currently supported model is `gpt-6-luna`.
[Official changelog](https://developers.openai.com/api/docs/changelog),
[Decisions guide](https://developers.openai.com/api/docs/guides/decisions).

Send `model`, a bounded text string in `input`, and one question in `questions`:
`type: score`, `name: effort`, rubric `instructions`, and ordered `levels` with
`label` and `description`. Use low, medium, high in that order. The native score
is the probability-weighted mean of zero-based level indices, so these three
levels produce a fractional `0..2` score. No scale conversion is needed.
[Decisions guide](https://developers.openai.com/api/docs/guides/decisions).

The response has an `answers` array; a score answer carries `name`, `type`, and
`score`, with probabilities and confidence. A question may return `type: refusal`
instead. The documented request does not require remote rubric creation or
polling. Use the synchronous endpoint without Responses-specific parameters.
[Create decision reference](https://developers.openai.com/api/reference/resources/decisions/methods/create).

> 🧠 **From Hindsight memory (Custom classifier provider and local System One trials)** — the adapter boundary preserves explicit provider selection, strict finite `0..2` scores, and fail-open behavior without fallback. These facts were checked against current `scorers.py`, `rubric.py`, and `middleware.py`.

The current code has four scorers (`jev`, `openrouter`, `cloudflare`, `custom`) and
four modes (`auto`, `once`, `always`, `off`). The old plan's `recommend` references
are obsolete. Enabling a mode authorizes sharing with the selected scorer;
there is no separate provider-consent setting. `timeout_s=3.0` is a transport
timeout, not a delay between classifications.

## Proposed configuration

These are plugin design choices, separate from OpenAI's wire contract:

| Setting or credential | Proposed behavior |
| --- | --- |
| `scorer_provider` | Add `openai_decision`; Jev remains the default |
| `scorer_model` | Reuse it; empty means `gpt-6-luna` for this provider |
| Explicit `scorer_model` | Send the configured identifier; report it accurately; API rejection fails open |
| `OPENAI_API_KEY` | Resolve lazily through Hermes secret scope, then environment |
| Endpoint | Fixed OpenAI Decisions URL; existing `endpoint` remains Jev-specific |
| Shared settings | Reuse `timeout_s`, `prompt_chars`, and `classification_instructions` |

No additional config key is needed. Update the model field's label/description
to explain its different requirements: required for OpenRouter/custom, optional
with a default for OpenAI Decisions. Do not silently substitute a model after
an error. Account access is verified by an operator probe, not a status request.

## Implementation sequence

The sequence below has been completed. The live API probe remains an operator follow-up;
the implementation and deterministic validation do not require account access.

1. **Establish the baseline.** Recheck the working tree and run the canonical
   tests/lint before code changes. There are existing edits in middleware,
   Desktop, docs, and tests; preserve them and distinguish baseline failures
   from new failures. Read the current contracts before integrating with those
   edits. Record the initiative after the user approves this plan, before code.

2. **Add a pure rubric builder and parser in `rubric.py`.** Build an independent
   Decisions question from the existing rubric definitions, preserving their
   wording and low/medium/high ordering. Adapt the instruction's `state.prompt`
   reference to Decisions' supplied input; leave existing System One requests
   unchanged. Append normalized operator guidance under its existing 2000-character
   cap, preserving the fixed score contract. Require exactly one answer named
   `effort`, with `type: score`, then use `numeric_score()` to validate it.
   Refusals, missing/mismatched names, extra answers, wrong types, invalid JSON,
   booleans, strings, non-finite values, and out-of-range scores return
   `malformed_response`. Ignore confidence/probabilities for routing; introduce
   no new confidence threshold or rounding.

3. **Add `openai_decision_client.py`.** Implement `classify()` and
   `classify_detail()` with injectable transport/key reader and stdlib HTTP.
   Use the existing head/tail task truncation and the shared rubric builder.
   Perform no HTTP call for invalid input or a missing key. Make one POST with
   the configured timeout; do not retry or follow redirects. Close responses
   and preserve existing transport/failure codes. Handle errors without logging
   response bodies, exception text, prompts, guidance, or credentials. Keep
   Hermes imports lazy; add no SDK/runtime dependency.

4. **Integrate settings and observability.** Extend `scorers.py` construction,
   credential-required/presence checks, fixed endpoint reporting, and effective
   model reporting. Add the provider choice to `plugin.yaml`, with defaults and
   schema aligned. Existing `middleware._settings()` should consume the registry's
   effective model/endpoint. Verify `/hae status`, `status json`, and `probe`
   display the selected provider correctly. Dashboard and Desktop already consume
   generic scorer fields; extend them only if an actual compatibility gap appears.
   Keep their allowlists and existing schema versions. Raw API response metadata
   must not enter session records or feeds.

5. **Validate with fake transports.** Add meaningful adapter/rubric coverage
   and extend provider-selection, config-schema, command, middleware, and real
   dispatcher tests at their existing boundaries. Keep the suite network-free
   and settings hermetic. Reuse shared routing tests for unchanged behaviors;
   exercise the new provider through the real registry and middleware at least
   once. See the acceptance checks below.

6. **Document and review.** README, CONTRACTS, HANDOFF, DEVELOPMENT's test
   inventory, and AGENTS' provider/layout descriptions have been updated. The
   final diff was reviewed and both canonical checks pass.

## Acceptance checks

- Request fixture matches the documented Decisions structure, with only one
  effort question, correct level order, bounded task text, and bounded guidance.
- Empty model uses `gpt-6-luna`; explicit models remain explicit in transport and
  status. The selected provider never falls back to another scorer.
- Credential lookup uses secret scope then environment. Missing keys make zero
  HTTP calls; status only checks presence and never classifies.
- Scores at `0`, `0.5`, `1.5`, and `2`, plus intermediate values, pass through the
  unchanged low/medium/high thresholds and route clamping. Invalid scores and
  refusal/malformed answers leave the original LLM request untouched.
- HTTP 401/403/429/5xx, timeout, connection failure, redirects, decoding errors,
  and unexpected exceptions fail open without retry or body/secret disclosure.
- Enabled routing makes at most one scorer call per turn, reuses it through tool
  loops and route changes, and memoizes failures in the selected scope.
- Off mode, disabled reasoning, and unsupported request controls make zero calls.
  Child routing retains its independent gate; the applied-change feed records
  only real rewrites and deduplicates them as before.
- Probe shares only operator-typed text plus configured guidance, stores no
  decision, and leaks neither input nor guidance in output/logs/events.
- Existing tests continue to cover concurrent claims, bounded stores, retained
  route behavior, and route re-clamping. Do not redesign the routing/cache logic
  to add a scorer.

Run exactly:

```powershell
.\scripts\run_tests.ps1
.\scripts\run_lint.ps1
```

Do not skip real dispatcher integration or install the payload with pip.

## Verification

The final canonical Windows run passed all **503 tests**, including the new
adapter and real-dispatcher coverage. Ruff reports `All checks passed!`. The
test run used a fresh temporary directory to avoid stale Windows ACLs under the
default pytest temp path. Tests use fake transports; no live API request was made.

## Live evaluation and remaining limits

Use operator-typed `/hae probe` examples to verify account access and score
quality. Measure actual latency and timeout frequency against the 3-second
default before enabling routing. A small labeled task set should cover
low/medium/high and ambiguous boundary cases; comparisons with other scorers
must use the same tasks and guidance. These measurements are separate from
deterministic unit-test success.

OpenAI documents ZDR support for eligible customers; that does not establish that
this operator's project has it enabled. Document provider retention separately
from the plugin's local no-prompt-storage contract.
[Decisions guide](https://developers.openai.com/api/docs/guides/decisions).

Beta availability and the API contract are verified against the official guide.
At the 2026-10-06 implementation checkpoint, this operator's account access, live
scorer timing/quality and total cost/cache effects were unmeasured. No live Decisions
request or operator configuration change was made, and deployment was not verified.
See the [current handoff](../HANDOFF.md) for later source/publication evidence.
