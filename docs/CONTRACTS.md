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

`cache_safe`, `inject`, and an unsafe `auto` injection narrow the key further, to the session, when an effort change would
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
| `muse-spark-1.3` | `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | OpenCode Zen Responses; standard 1.3 only supports `max` |
| `muse-spark-1.2` | `minimal`, `low`, `medium`, `high`, `xhigh` | OpenCode Zen Responses |
| `muse-spark-1.3-contributor-free` | `minimal`, `low`, `medium`, `high`, `xhigh` | OpenCode Zen Responses; contributor tiers exclude `max` |
| `muse-spark-1.3-contributor`, `muse-spark-1.2-contributor` | `minimal`, `low`, `medium`, `high`, `xhigh` | OpenCode Go Responses |
| any other non-Codex route | `route_supported_efforts(provider, model)` | for an unknown route this is the widest OpenAI-compatible set |
| `openai-codex` | `route_supported_efforts(...)` | the narrow `wire_efforts` table is skipped for this provider |

Hermes effort ladder used by the verified host:

```python
EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
OPENAI_COMPAT_WIRE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
```

`ultra` is Hermes-internal and appears in no wire set. Unset stays unset for `off`, `recommend`, and `cache_safe`. `auto` and the retained `inject` mode may add an effort only on the exact provider/model/API routes in the README, with copy-on-write of the request and reasoning container. Disabled/malformed controls remain untouched. Injection transitions use `absent` as the prior value in the applied-change feed.

### Residual risk: Ox Alpha / `x-preview-f-free`

OpenCode "Ox Alpha" accepts exactly `low`/`high`/`max`; `medium` is a 400. The core
declares `OX_ALPHA_EFFORTS` and `OX_ALPHA_OVERRIDES` but exposes **no route selector**
for that slug, so `route_supported_efforts` hands this plugin the wide set and the plugin
can legitimately choose `medium` — which the vendor then rejects. This is a live, unfixed
risk, tracked by criterion 8 of the card and **not** worked around here (a follow-up
would add the Ox Alpha slug to `wire_efforts()`/`wire_overrides()` in `hermes-adaptive-effort/effort.py`,
mirroring the Kimi and GLM entries that are already there).

## Fail-open behaviour

Scorer selection is explicit: `jev` (default), `openrouter`, or `cloudflare`; failures never select another scorer. OpenRouter requires `scorer_model` and `OPENROUTER_API_KEY`. Cloudflare uses `cloudflare_model` (`clef` by default, or `clef-flash`) to select the matching Workers AI route and body selector; it requires a 32-character hexadecimal `cloudflare_account_id` and `CLOUDFLARE_AUTH_TOKEN`, and ignores the Jev endpoint and OpenRouter model settings. Credentials resolve through Hermes secret scope, then environment, and are never rendered in status.

Cloudflare returns the shared `0..2` score from `result.answers.effort.score` only when the REST wrapper has `success: true` and no reported errors. Missing or invalid account IDs fail before HTTP with `account_missing` or `account_invalid`; other failures use the existing transport/response codes. Status adds `cloudflare_account_ready`, separate from token presence, and displays the account-scoped endpoint (a placeholder when the ID is invalid). Probe scores only operator-typed text and stores no decision.

| situation | request | request state | scorer calls |
| --- | --- | --- | --- |
| no credential | unchanged | `failed` (`credential_missing`) | 0 HTTP calls |
| classifier timeout / transport error | unchanged | `failed`, one probe | 1 (never retried in the turn) |
| score out of `0..2`, non-finite, non-numeric | unchanged | `failed`, one probe | 1 |
| no field and route lacks positive injection support (or reasoning disabled) | unchanged | `unsupported` | 0 |
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

When `auto` injects on an eligible but cache-unsafe route, it uses the same session pin. The
injection marker keeps that decision pinned even if later requests already carry the injected
field and would otherwise follow the ordinary per-turn `auto` key.

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

Existing codes remain stable: `invalid_prompt`, `credential_missing`, `http_error`, `timeout`, `transport_error`, `malformed_response`, `unexpected_error`, `classifier_error`. Scorer selection adds `model_missing`, `unsupported_provider` . Failed decisions are not retried within their retained scope. Missing credentials perform no HTTP call, even though the ledger records a classification attempt.

## Privacy boundary

Enabling routing authorizes task text to the selected scorer. Normal turns send at most `prompt_chars` characters of the latest user text; the conversation history and tool results are not sent as task text. Child scoring uses the parent-written goal and requires the independent subagent mode to be enabled. Explicit probe commands send only operator-typed text, even when routing is off. The removed `prompt_sharing_provider` setting is ignored for legacy configurations. Prompts are not persisted or emitted in logs, reasons, status, probe output or the applied-change feed. Child goals necessarily exist transiently in memory. These are plugin guarantees, not statements about a scoring provider's retention policy. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; the prompt is still processed by OpenRouter. Jev and Cloudflare have no ZDR guarantee from this plugin. `prompt_chars` limits the excerpt only and is not a retention control.

## Verified Muse transport path

The local Hermes source (`agent/transports/codex.py`, module-level `_reasoning_fields()`, called by `ResponsesApiTransport.build_kwargs()`) builds `fields["reasoning"] = {"effort": effort, "summary": "auto"}`. `build_kwargs` merges those fields into top-level kwargs. `agent/codex_responses_adapter.py` retains `reasoning` as an optional dict and preflight copies it unchanged. `agent/turn_api_request.py` applies request middleware after preflight and uses its returned payload. Thus injection belongs in top-level `reasoning.effort`. The allowlist combines that verified Hermes container with exact OpenCode Zen/Go model IDs whose vendor docs declare an effort control; it does not authorize Chat Completions or infer support from the broad OpenAI-compatible fallback.
