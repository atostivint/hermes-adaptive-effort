# Runtime contracts

Current implementation reference. For installation and everyday use, start with the [README](../README.md). For the reasons behind these contracts, see [design choices](DESIGN.md).

## Decision cache: one decision per turn, valid only for its route

The memo key is `(session_id, turn_id)`:

* Each new turn classifies the **latest user text** in `messages` or Codex `input`,
  skipping assistant and tool results. Earlier conversation history is not the new task.
* A multi-call turn (a tool loop) is **several requests of one turn**: it reuses the
  decision from its first call — one scorer call, not one per request. `api_call_count` is
  not consulted; the turn id is the only authority.
* The entry records the **provider and the model** the decision was made on, plus the
  label, the target, the request/probe counters and the outcome (`state`).
* On reuse, the recorded *target* is **re-clamped onto the current route**. The label
  describes the prompt and stays valid; the wire value is per route and does not. This is
  what protects a provider fallback inside one turn: a `medium` recorded on a wide route
  is never replayed verbatim onto a route that spells its middle level `high`.
* `failed` and `unsupported` outcomes are **not retried inside the turn** — a classifier
  outage costs at most one probe per turn, not one per request.

These guarantees apply while a decision remains cached; eviction or state reset removes that memory.

`cache_safe` narrows the key further, to the session, when an effort change would
invalidate the prompt cache (see below).

## Route vocabulary: what may be written where

The plugin uses Hermes' `agent/reasoning_effort.py` for the
route, and adds the narrow vendor sets that the host's *entry* clamp does not apply,
because an `llm_request` hook runs **after** the transport clamp.

| route (bare model slug) | vocabulary used | notes |
| --- | --- | --- |
| Kimi/Moonshot slugs identified as K3 by Hermes | `low`, `high`, `max` | `medium` rounds **up** to `high` (K3's positional middle and server default) |
| Kimi K2-era slugs detected by Hermes | `low`, `medium`, `high` | Hermes' Kimi detection distinguishes these from K3 |
| `glm-5.2*` | `high`, `max` | `low` and `medium` are not offered by this route |
| `glm-5.3*` | `low`, `medium`, `high`, `max` | graded scale, monotonic in reasoning tokens |
| any other non-Codex route | `route_supported_efforts(provider, model)` | for an unknown route this is the widest OpenAI-compatible set |
| `openai-codex` | `route_supported_efforts(...)` | the narrow `wire_efforts` table is skipped for this provider |

Hermes effort ladder used by the verified host:

```python
EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
OPENAI_COMPAT_WIRE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
```

`ultra` is Hermes-internal and appears in no wire set. Unset stays unset: the plugin
never introduces an effort where the request had none.

### Residual risk: Ox Alpha / `x-preview-f-free`

OpenCode "Ox Alpha" accepts exactly `low`/`high`/`max`; `medium` is a 400. The core
declares `OX_ALPHA_EFFORTS` and `OX_ALPHA_OVERRIDES` but exposes **no route selector**
for that slug, so `route_supported_efforts` hands this plugin the wide set and the plugin
can legitimately choose `medium` — which the vendor then rejects. This is a live, unfixed
risk, tracked by criterion 8 of the card and **not** worked around here (a follow-up
would add the Ox Alpha slug to `wire_efforts()`/`wire_overrides()` in `hermes-adaptive-effort/effort.py`,
mirroring the Kimi and GLM entries that are already there).

## Fail-open behaviour

Scorer selection is explicit: `jev` (default), `openrouter`, `cloudflare`, or `custom`; failures never select another scorer. The scorer is independent of the conversation model. OpenRouter requires `scorer_model` and `OPENROUTER_API_KEY`. Cloudflare uses `cloudflare_model` (`clef` by default, or `clef-flash`) to select the matching Workers AI route and body selector; it requires a 32-character hexadecimal `cloudflare_account_id` and `CLOUDFLARE_AUTH_TOKEN`. Custom requires `scorer_model`, a complete `custom_endpoint`, and `custom_api_format` (`systemone` by default or `chat_completions`). `custom_auth` defaults to `none`; `bearer` requires `CUSTOM_SCORER_API_KEY`. Required credentials resolve through Hermes secret scope, then environment, and are never rendered in status. Auth `none` does not look up a key.

The custom endpoint is used exactly as configured; no path is appended. It must be a valid HTTP(S) URL without embedded credentials or a fragment. Redirects are not followed. Status masks URL query values and adds the boolean `credential_required`; custom auth `none` reports false without inspecting a key, while `bearer` reports true and checks key presence without exposing the key. System One sends the bounded prompt in `state.prompt`, the shared `questions` rubric, and the configured model; it reads `answers.effort.score`. Chat Completions uses a JSON response request with zero temperature, at most 32 completion tokens, no streaming, and the configured model; only the strict numeric score is accepted. Both formats share the rubric and finite `0..2` validation. Invalid endpoint and unsupported format/auth settings fail open before transport.

Cloudflare returns the shared `0..2` score from `result.answers.effort.score` only when the REST wrapper has `success: true` and no reported errors. Missing or invalid account IDs fail before HTTP with `account_missing` or `account_invalid`; other failures use the existing transport/response codes. Status adds `cloudflare_account_ready`, separate from token presence, and displays the account-scoped endpoint (a placeholder when the ID is invalid). Probe scores only operator-typed text and stores no decision.

| situation | request | request state | scorer calls |
| --- | --- | --- | --- |
| missing required credential | unchanged | `failed` (`credential_missing`) | 0 HTTP calls |
| custom auth `none` | unchanged unless classification succeeds | configured result | no credential lookup |
| custom endpoint/model/format/auth invalid | unchanged | `failed` with configuration code | 0 HTTP calls |
| classifier timeout / transport error | unchanged | `failed`, one probe | 1 (never retried in the turn) |
| score out of `0..2`, non-finite, non-numeric | unchanged | `failed`, one probe | 1 |
| no writable effort field (e.g. reasoning disabled) | unchanged | `unsupported` | 0 |
| label has no legal level on the route | unchanged | `unsupported` | possibly 1; the score may already exist |
| any internal exception in the plugin | unchanged | — | — |

Scorer and plugin failures leave the request as the host built it. A provider can still reject a rewritten value when its declared vocabulary is inaccurate; the Ox Alpha gap above is an example.

`unsupported` with `probes=0` can mean the Hermes provider profile emitted no effort
field, even if the model generates reasoning. Verified on the current OpenCode Go
profile in the dated live evaluation: `space-bunny-free` emits no writable field; Kimi K2 and DeepSeek can emit
top-level `reasoning_effort` when reasoning is configured. Model reasoning support
alone does not establish a working effort control on a particular provider route.
Classification each turn also does not imply a different value each turn: a matching
value produces no rewrite, and narrow vocabularies can map every rubric label to the
same effort (GLM-5.2 maps `low`/`medium`/`high` to `high`).

## Prompt cache

An effort change is visible to the cache layer only when the route renders the thinking
configuration into the prompt (Anthropic-style `anthropic_messages` routes). `cache_safe`
therefore:

* keeps the **per-turn** key on routes where an effort change is cache-neutral
  (`chat_completions`, `codex_responses`);
* drops the turn id and **pins one level for the whole session** on cache-hostile routes,
  so later turns reuse the first decision while it remains in the bounded cache. Eviction, reload or a session reset can require a new classification.

What is **not** measured: the real effect on `cache_read_tokens` / cost on this box. That
would need an A/B run against the live provider; no such measurement was performed, and
nothing in this repository claims a number for it.

## Subagents

Registered child sessions are classified from the goal written by their parent. `subagent_mode` is an independent gate and defaults to `off`; setting the main mode to `auto` does not enable child rewrites. The main mode must also be enabled, since its `off` gate exits before child handling. Child goals are held transiently in a separate bounded in-memory registry and removed on child stop or session cleanup; they are not logged or exposed in status. Child decisions use their own session/turn identity.

## Public API and visibility

The slash command is `/hermes-adaptive-effort`. `status` performs no scoring; `probe <text>` scores only operator-typed text and stores no decision. Mode commands apply only to future requests in the current process.

The dashboard API is mounted under `/api/plugins/hermes-adaptive-effort/`:

| Method and path | Contract |
| --- | --- |
| `GET /status` | `hermes-adaptive-effort.status.v1`; allowlisted decisions and route/scorer metadata |
| `GET /changes` | `hermes-adaptive-effort.changes.v1`; bounded applied-change feed |
| `POST /mode` | Persist the mode through the host settings API and apply it to the process |
| `POST /probe` | `hermes-adaptive-effort.probe.v1`; score/label/failure and character count, no prompt echo |

The applied-change feed contains `{stream_id, events: [{id, from, to, at}], latest}`. It retains up to 64 actual rewritten transitions, without prompt or session identifiers. Recommend mode, unsupported/failed attempts and identical values emit no change. Deduplication uses `(decision_key, from, to)`; a reload/reset creates a new stream ID and consumers must reset their cursor.

The Desktop chip matches the focused chat's exact `conversation_id` and backend/profile owner. It polls every two seconds and does not use global changes as a fallback for another chat's effort. No decision, unsupported/in-flight state or backend mismatch shows `Effort: N/A`. The pane's latest transition and change notifications apply across conversations, since the feed has no conversation identifier. Initial history establishes a baseline without replaying old notifications. The Desktop extension is opt-in.

Completed-turn `on_session_end` events with a turn ID retain bounded decision state. Actual finalize/reset hooks clear it; legacy end events without a turn ID retain cleanup behavior. This lets the chip keep a completed turn's result while the entry remains cached.

The CLI uses the optional host `register_cli_status_item` API to show the last applied effort and a short notice. Older hosts still have command status and prompt-free log notices.

## Failure codes

Existing codes remain stable: `invalid_prompt`, `credential_missing`, `http_error`, `timeout`, `transport_error`, `malformed_response`, `unexpected_error`, `classifier_error`. Scorer configuration adds `model_missing`, `endpoint_missing`, `endpoint_invalid`, `unsupported_api_format`, `unsupported_auth`, `unsupported_provider` and `prompt_consent_required`. Failed decisions are not retried within their retained scope. Missing consent or credentials perform no HTTP call, even though the ledger records a classification attempt.

## Privacy boundary

Normal turns send at most `prompt_chars` characters of the latest user text only when `prompt_sharing_provider` matches the selected scorer; the conversation history and tool results are not sent as task text. Child scoring uses the parent-written goal and the same consent gate. Without matching consent, no scorer call is made and the decision fails open with `prompt_consent_required`. Prompts are not persisted or emitted in logs, reasons, status, probe output or the applied-change feed. Child goals necessarily exist transiently in memory. These are plugin guarantees, not statements about a scoring provider's retention policy. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; the prompt is still processed by OpenRouter. Jev, Cloudflare, and custom endpoints have no ZDR guarantee from this plugin. `prompt_chars` limits the excerpt only and is not a retention control.
