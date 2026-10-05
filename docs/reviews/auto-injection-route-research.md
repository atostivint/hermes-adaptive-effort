# Auto injection route research

Date: 2026-10-05

## Recommendation

Make `auto` inject a missing effort only when a positive route entry matches the provider, exact model ID, API mode, and request field shape. Keep the global default mode `off`. Continue rewriting existing supported fields as before. Unknown routes, unknown model slugs, unknown API modes, disabled controls, malformed containers, and broad OpenAI-compatible vocabularies are not evidence that a route accepts effort; they must remain no-probe no-ops when no existing field is writable. `inject` can remain as a compatibility alias if desired.

For the current OpenCode Zen and Go catalog, the bounded first registry can cover Muse Spark only:

| Hermes provider | Exact model ID | `api_mode` | Field to add | Allowed effort values |
| --- | --- | --- | --- | --- |
| `opencode-zen` | `muse-spark-1.3` | `codex_responses` | top-level `reasoning.effort` | `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `opencode-zen` | `muse-spark-1.2` | `codex_responses` | top-level `reasoning.effort` | `minimal`, `low`, `medium`, `high`, `xhigh` |
| `opencode-zen` | `muse-spark-1.3-contributor-free` | `codex_responses` | top-level `reasoning.effort` | `minimal`, `low`, `medium`, `high`, `xhigh` |
| `opencode-go` | `muse-spark-1.3-contributor` | `codex_responses` | top-level `reasoning.effort` | `minimal`, `low`, `medium`, `high`, `xhigh` |
| `opencode-go` | `muse-spark-1.2-contributor` | `codex_responses` | top-level `reasoning.effort` | `minimal`, `low`, `medium`, `high`, `xhigh` |

The route entries use the canonical Hermes profile names. Hermes declares Zen aliases `opencode`, `opencode_zen`, and `zen`; Go is separate, with aliases `opencode_go`, `go`, and `opencode-go-sub`. Do not treat `opencode` and `opencode-go` as interchangeable providers. The provider profile definitions are in the sibling Hermes source at `plugins/model-providers/opencode-zen/__init__.py`.

The official OpenCode Zen catalog lists standard Muse 1.2/1.3 and the 1.3 Contributor Free model on `/v1/responses`. The official Go catalog lists 1.2 and 1.3 Contributor models on `/zen/go/v1/responses`; both catalogs identify the OpenAI SDK package. Hermes routes the `muse-spark` family through `codex_responses` in `hermes_cli/models.py` (`opencode_model_api_mode`). The local Responses transport builds a top-level `reasoning` object, and the request adapter preserves it through middleware. The eligible injected container is therefore top-level `reasoning.effort`, copy-on-write with any existing siblings retained.

Meta's API reference documents Muse effort values `minimal`, `low`, `medium`, `high`, and `xhigh`; `none` is rejected with HTTP 400. It says `max` is available only for standard-tier Muse Spark 1.3, so do not send it to 1.2 or any Contributor route. The docs also show the analogous Chat Completions field `reasoning_effort`, but Zen/Go publish these catalog entries as Responses routes. That separate field shape is not sufficient evidence to enable Chat Completions injection on those providers.

## Why not other OpenCode models yet

The Hermes OpenCode provider profile contains specific chat-completion transformations for Ox Alpha (`x-preview-f-free`) and OpenCode Go GLM-5.2, Kimi K2, and DeepSeek V4. Those are potential follow-up entries, not generic support. They need exact route-level confirmation before injection is enabled: the Go families use different control combinations, and DeepSeek V4 requires the thinking toggle alongside effort and has multi-turn `reasoning_content` behavior. The Zen profile's exact Ox Alpha map and the shared wire-vocabulary table are useful evidence, but I found no primary vendor source here sufficient to justify expanding the first Muse-focused registry to those different paths.

Likewise, do not infer effort control from a reasoning-capable model label, an OpenAI-compatible API, an unknown model slug, or Hermes' broad fallback vocabulary. Some providers accept `reasoning`, some accept `reasoning_effort`, some require additional toggles, and some do not support a control. A generic field can turn a previously successful request into a provider 400.

## Cache and failure behavior

Use the existing `cache_safety.effort_is_cache_safe(provider, model, api_mode)` result for the decision scope. The proposed entries are `codex_responses`, already classified cache-safe, so classification remains per turn and repeated requests in the same turn reuse the stored decision and re-inject only when the incoming request still lacks the field. A future route entry on an unsafe API mode must use session-pinned decision scope. No prompt or credential value belongs in the route registry, status, failure reason, or applied-change event.

If scoring fails or yields an invalid value, leave the request unchanged. If no route entry matches, do not call the scorer just to record `unsupported`; preserve the established no-probe behavior for unsupported missing-field requests. Explicit `off` remains a no-op.

## Focused test targets

- Table-driven positive cases for the five routes above: each score label maps only into that route's exact vocabulary and writes only top-level `reasoning.effort`.
- Standard Muse 1.3 permits `max`; Muse 1.2 and every Contributor variant reject `max`; no Muse entry permits `none`.
- Copy-on-write preserves the original request and any sibling keys under `reasoning`.
- `auto` injects only for a positive registry match; `auto` and compatibility `inject` both leave unknown model slugs, provider/API-mode mismatches, and unverified request shapes untouched without a scorer call.
- Existing `reasoning.effort`, `reasoning_effort`, `extra_body.reasoning`, or an explicit disabled/malformed control is never overwritten by injection.
- Default `off` still performs zero scorer calls; existing-field rewrite behavior in `auto` remains intact.
- Same-turn reuse and shared cache-safety behavior hold for Zen/Go Responses routes; scorer exceptions, invalid scores, and malformed requests fail open.
- Command, settings schema, dashboard API, Desktop mode list, status, README, CONTRACTS, and HANDOFF all describe `auto` as conditional injection and preserve `off` as the default.

## Primary sources

- [OpenCode Zen model catalog](https://dev.opencode.ai/docs/zen/): standard Muse 1.2/1.3 and Contributor Free 1.3 IDs and Responses endpoint.
- [OpenCode Go model catalog](https://dev.opencode.ai/docs/go/): Contributor 1.2/1.3 IDs and Go Responses endpoint.
- [Meta Model API model catalog](https://dev.meta.ai/docs/models): exact model versions and Standard/Contributor tiers.
- [Meta Model API reasoning reference](https://dev.meta.ai/docs/reasoning): effort vocabulary, rejection of `none`, and `max` limited to standard-tier 1.3.

This is source and route analysis only. No credentials were read and no live model or paid API probes were run. A catalog listing establishes documented support, not live availability for a particular account or region.
