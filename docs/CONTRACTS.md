# Runtime contracts

Current implementation reference. For installation and everyday use, start with the [README](../README.md). For the reasons behind these contracts, see [design choices](DESIGN.md).

## Decision scope: four public modes

The only public modes are `auto`, `once`, `always`, and `off`. The default is `off` for both the main session and subagents. `/hae` and the dashboard mode API accept only those names; unknown values disable the mode when read from configuration.

| mode | decision scope |
| --- | --- |
| `auto` | Per new user turn only for exact model/API routes explicitly registered for dynamic effort and whose transport is verified cache-neutral; otherwise one decision per session/provider/model/API route |
| `once` | One decision per session/provider/model/API route, reused on later turns |
| `always` | Per new user turn on any eligible route |
| `off` | No scorer call or effort change; bounded route metadata may remain for the Desktop popup |

An existing effort field or an `effort_models` declaration does not establish dynamic support. The dynamic registry currently includes the documented exact OpenCode Go/Zen control routes and `openai-codex/gpt-6.1-sol` on `codex_responses`; each must also pass the transport cache-safety check.

Configuration accepts only the four canonical mode values. Unknown values become `off`; the plugin does not translate older mode names or rewrite the operator's config file.

Turn decisions and persistent route decisions live in separate ledgers, each bounded by `max_turns`. `once` returns to a previously evaluated model/API route without another scorer call. If a route changes during a tool loop, the current turn's label remains authoritative, is re-clamped for the new route, and seeds a route decision only if that route has no prior pin. A new session, eviction, process reload, or reset can require another classification. Without a turn ID, active modes use the persistent session/route scope.

## Turn memo

The memo key is `(session_id, turn_id)`:

* Each new turn classifies the **latest user text** in `messages` or Codex `input`,
  skipping assistant and tool results. Earlier conversation history is not the new task.
* A multi-call turn (a tool loop) is **several requests of one turn**: it reuses the
  decision from its first call — one scorer call across route changes, not one per request.
  `api_call_count` is not consulted; the turn id is the only authority.
* The entry records the **provider and the model** the decision was made on, plus the
  label, the target, the request/probe counters and the outcome (`state`).
* On reuse, the recorded *target* is **re-clamped onto the current route**. The label
  describes the prompt and stays valid; the wire value is per route and does not. This is
  what protects a provider fallback inside one turn: a `medium` recorded on a wide route
  is never replayed verbatim onto a route that spells its middle level `high`.
* `failed` and `unsupported` outcomes are **not retried inside the turn** — a classifier
  outage costs at most one probe per turn, not one per request.

These guarantees apply while a decision remains in the bounded ledger; eviction or state reset removes that memory.

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

Adding a missing effort field has its own exact provider/model/API registry, separate from this route clamp. Its current OpenCode Go entries and the no-op outcome for each published catalog model are listed in the [model compatibility matrix](MODEL_COMPATIBILITY.md). The optional `effort_models` setting is an empty-by-default, comma/newline-separated exact model-ID list. It strips a leading namespace for matching and authorizes the selected model on any provider only for known `codex_responses` or `chat_completions` containers. It is an operator assertion, not vendor evidence; no globbing, Anthropic shape inference, or unknown API-mode field addition is allowed. Recognized per-model vocabulary and paired-control guards still apply.

Hermes effort ladder used by the verified host:

```python
EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
OPENAI_COMPAT_WIRE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
```

`ultra` is Hermes-internal and appears in no wire set. Unset stays unset for `off`. Every active mode may add an effort only on an exact registry route or for an operator-listed model on the two recognized OpenAI-compatible carriers, with copy-on-write of the request and reasoning container. Disabled/malformed controls remain untouched. Field-addition transitions use `absent` as the prior value in the applied-change feed.

### Residual risk: Ox Alpha / `x-preview-f-free`

OpenCode "Ox Alpha" accepts exactly `low`/`high`/`max`; `medium` is a 400. The core
declares `OX_ALPHA_EFFORTS` and `OX_ALPHA_OVERRIDES` but exposes **no route selector**
for that slug, so `route_supported_efforts` hands this plugin the wide set and the plugin
can legitimately choose `medium` — which the vendor then rejects. This is a live, unfixed
risk, tracked by criterion 8 of the card and **not** worked around here (a follow-up
would add the Ox Alpha slug to `wire_efforts()`/`wire_overrides()` in `effort.py`,
mirroring the Kimi and GLM entries that are already there).

## Fail-open behaviour

Scorer selection is explicit: `jev` (default), `openrouter`, `cloudflare`, or `custom`; failures never select another scorer. The scorer is independent of the conversation model. OpenRouter requires `scorer_model` and `OPENROUTER_API_KEY`. Cloudflare uses `cloudflare_model` (`clef` by default, or `clef-flash`) to select the matching Workers AI route and body selector; it requires a 32-character hexadecimal `cloudflare_account_id` and `CLOUDFLARE_AUTH_TOKEN`. Custom requires `scorer_model`, a complete `custom_endpoint`, and `custom_api_format` (`systemone` by default or `chat_completions`). `custom_auth` defaults to `none`; `bearer` requires `CUSTOM_SCORER_API_KEY`. Required credentials resolve through Hermes secret scope, then environment, and are never rendered in status. Auth `none` does not look up a key.

The custom endpoint is used exactly as configured; no path is appended. It must be a valid HTTP(S) URL without embedded credentials or a fragment. Redirects are not followed. Status masks URL query values and adds the boolean `credential_required`; custom auth `none` reports false without inspecting a key, while `bearer` reports true and checks key presence without exposing the key. System One sends the bounded prompt in `state.prompt`, the shared `questions` rubric, and the configured model; it reads `answers.effort.score`. Chat Completions uses a JSON response request with zero temperature, at most 32 completion tokens, no streaming, and the configured model; only the strict numeric score is accepted. Both formats share the rubric and finite `0..2` validation. Invalid endpoint and unsupported format/auth settings fail open before transport.

Cloudflare returns the shared `0..2` score from `result.answers.effort.score` only when the REST wrapper has `success: true` and no reported errors. Missing or invalid account IDs fail before HTTP with `account_missing` or `account_invalid`; other failures use the existing transport/response codes. Status adds `cloudflare_account_ready`, separate from token presence, and displays the account-scoped endpoint (a placeholder when the ID is invalid). Probe scores operator-typed text plus optional configured guidance and stores no decision.

| situation | request | request state | scorer calls |
| --- | --- | --- | --- |
| missing required credential | unchanged | `failed` (`credential_missing`) | 0 HTTP calls |
| custom auth `none` | unchanged unless classification succeeds | configured result | no credential lookup |
| custom endpoint/model/format/auth invalid | unchanged | `failed` with configuration code | 0 HTTP calls |
| classifier timeout / transport error | unchanged | `failed`, one probe | 1 (never retried in the turn) |
| score out of `0..2`, non-finite, non-numeric | unchanged | `failed`, one probe | 1 |
| no field and route lacks positive support for adding one (or reasoning disabled) | unchanged | `unsupported` | 0 |
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
configuration into the prompt (Anthropic-style `anthropic_messages` routes). `auto`
therefore uses per-turn decisions only when both an exact model/API capability and a
cache-neutral transport are registered. Other routes keep a decision per exact
session/provider/model/API route. `once` always uses that route scope; `always` uses
per-turn scope regardless of the route's cache classification. Eviction, reload or a
session reset can require a new classification.

What is **not** measured: the real effect on `cache_read_tokens` / cost on this box. That
would need an A/B run against the live provider; no such measurement was performed, and
nothing in this repository claims a number for it.

When `auto` adds a field on a route without verified dynamic support, the decision is
retained under the same route identity as rewrites to existing fields. An incoming field
added by an earlier request remains subject to that route decision.

## Subagents

Registered child sessions are classified from the goal written by their parent. `subagent_mode` is an independent gate and defaults to `off`; setting the main mode to `auto` does not enable child rewrites. The main mode must also be enabled, since its `off` gate exits before child handling. Child goals are held transiently in a separate bounded in-memory registry and removed on child stop or session cleanup; they are not logged or exposed in status. Child decisions use their own session/turn identity.

## Public API and visibility

The plugin registers the `/hae` slash command. `status` performs no scoring; `probe <text>` scores operator-typed text plus optional configured guidance and stores no decision. Mode commands apply only to future requests in the current process.

The dashboard API is mounted under `/api/plugins/hermes-adaptive-effort/`:

| Method and path | Contract |
| --- | --- |
| `GET /status` | `hermes-adaptive-effort.status.v1`; allowlisted decisions and route/scorer metadata |
| `GET /changes` | `hermes-adaptive-effort.changes.v1`; bounded applied-change feed |
| `POST /mode` | Persist the mode through the host settings API and apply it to the process |
| `POST /probe` | `hermes-adaptive-effort.probe.v1`; score/label/failure and character count, no prompt echo |

The applied-change feed contains `{stream_id, events: [{id, from, to, at}], latest}`. It retains up to 64 actual rewritten transitions, without prompt or session identifiers. `off`, unsupported/failed attempts and identical values emit no change. Deduplication uses `(decision_key, from, to)`; a reload/reset creates a new stream ID and consumers must reset their cursor.

The Desktop chip matches the focused chat's exact `conversation_id` and backend/profile owner. Its popup groups the active mode and description, focused conversation effort and route, selected scorer/model and readiness, and whether custom classifier guidance is configured. It polls every two seconds and does not use global changes as a fallback for another chat's effort. No decision, unsupported/in-flight state or backend mismatch shows `Effort: N/A`. The pane's latest transition and change notifications apply across conversations, since the feed has no conversation identifier. Initial history establishes a baseline without replaying old notifications. The Desktop extension is opt-in.

When mode is `off`, the middleware may keep bounded provider/model/API-mode status for the current route so the popup can identify it. It makes no scorer call, stores no prompt, and leaves the request unchanged; this metadata does not create an effort decision.

Completed-turn `on_session_end` events with a turn ID retain bounded decision state. Actual finalize/reset hooks clear it; legacy end events without a turn ID retain cleanup behavior. This lets the chip keep a completed turn's result while the entry remains cached.

The CLI uses the optional host `register_cli_status_item` API to show the last applied effort and a short notice. Older hosts still have command status and prompt-free log notices.

`config_schema` also exposes `show_tui_status` and `show_desktop_popup`, both enabled by default. The former controls the TUI effort status item and is re-read as requests arrive. The latter hides the bottom-right Desktop chip, its mode-selection popup, and its change notifications; the separate details pane remains available. These switches affect display only, not routing or classification.

## Rubric and configurable guidance

The shared score contract remains numeric `0..2` → `low` / `medium` / `high`; operator text cannot replace the score format or the built-in level definitions. `classification_instructions` is an optional addition, capped at 2,000 characters, and is added to the selected scorer's rubric for Jev, OpenRouter, Cloudflare and custom providers. The empty default preserves the built-in rubric. The setting is available in Desktop Capabilities → Plugins and in `plugins.entries.hermes-adaptive-effort.settings`.

Jev's System One request uses the configured `jev_model`, whose default is `jev-latest`. The
setting is a free-form model name passed to the selected Jev endpoint; it is displayed as the
effective scorer model in status. An empty value falls back to the default. OpenRouter and
custom continue to use `scorer_model`, while Cloudflare uses `cloudflare_model`.

The default rubric considers requested complexity, ambiguity, scope, reasoning steps, tool or research depth, and explicit speed/cost priorities. This is informed by Anthropic's [Effort guide](https://platform.claude.com/docs/en/build-with-claude/effort), which describes effort as a thoroughness/token-efficiency trade-off and gives typical examples by task type. Anthropic's recommendations are model-specific; this plugin adopts them only as qualitative guidance. No quality, latency or cost improvement has been measured here.

## Failure codes

Existing codes remain stable: `invalid_prompt`, `credential_missing`, `http_error`, `timeout`, `transport_error`, `malformed_response`, `unexpected_error`, `classifier_error`. Scorer selection adds `model_missing`, `unsupported_provider`, plus `account_missing` / `account_invalid` (Cloudflare) and `endpoint_missing`, `endpoint_invalid`, `unsupported_api_format`, `unsupported_auth` (custom). Failed decisions are not retried within their retained scope. Missing credentials perform no HTTP call, even though the ledger records a classification attempt.

## Privacy boundary

Enabling routing authorizes sending task text and any configured classification guidance to the selected scorer. Normal turns send at most `prompt_chars` characters of the latest user text, plus up to 2,000 characters of operator guidance; the conversation history and tool results are not sent as task text. Child scoring uses the parent-written goal and requires the independent subagent mode to be enabled. Explicit probe commands send only operator-typed text plus the configured guidance, even when routing is off. Prompts and guidance are not persisted or emitted in logs, reasons, status, probe output or the applied-change feed; status exposes only whether guidance is configured and its character count. Child goals necessarily exist transiently in memory. These are plugin guarantees, not statements about a scoring provider's retention policy. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; the prompt is still processed by OpenRouter. Jev, Cloudflare, and custom endpoints have no ZDR guarantee from this plugin. `prompt_chars` limits the user-text excerpt only and is not a retention control.

## Verified transport paths

For Responses, local Hermes `agent/transports/codex.py` module-level `_reasoning_fields()` (called by `ResponsesApiTransport.build_kwargs()`) builds `fields["reasoning"] = {"effort": effort, "summary": "auto"}`. The adapter and preflight retain the optional top-level object, and `turn_api_request.py` applies middleware after preflight. Thus the Responses carrier is top-level `reasoning.effort`. For Chat Completions, the verified carriers are top-level `reasoning_effort`; DeepSeek V4 and GLM-5.3 entries additionally require that the incoming request already has `extra_body.thinking.type="enabled"`. The plugin never adds that toggle. The [model compatibility matrix](MODEL_COMPATIBILITY.md) records exact OpenCode Zen/Go model outcomes and primary sources; broad OpenAI-compatible fallback alone never enables injection.
