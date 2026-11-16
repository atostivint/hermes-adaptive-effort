# Model compatibility

[Configuration](CONFIGURATION.md) · [Runtime contracts](CONTRACTS.md) · [Historical evidence](README.md#historical-evidence)

Registry/source review: 2026-10-07; per-route table and cache evidence reviewed 2026-11-14. Catalog entries below are dated evidence, not a live model directory.

This page separates three kinds of evidence: vendor-documented effort controls, Hermes request routing, and actual completed requests observed with this plugin. A model appearing in the OpenCode Go catalog does not by itself prove that it accepts a reasoning-effort field. [One table below](#other-tested-routes-and-coverage-boundaries) states, per exact route family, which field is writable, which levels the scorer may choose, whether the plugin may add a missing field, whether effort may change every turn, and what was measured. Missing-field injection exists only where that table says *may add*: an exact registry entry, or an exact `effort_models` ID on a known Responses/Chat Completions carrier. Every other route is a no-op when the field is absent. Existing request fields continue to use the ordinary rewrite path, subject to the route vocabulary and disabled/malformed-control guards.

The OpenCode Go documentation reviewed on 2026-10-07 lists 30 curated model names and provides an endpoint table. A public unauthenticated model-catalog GET at `https://opencode.ai/zen/go/v1/models` returned 43 IDs on 2026-10-05. The table below is the union of that snapshot and the current docs list: 44 exact IDs, including the current `space-bunny` ID and the older `space-bunny-free` snapshot alias. IDs not in the current docs endpoint table are marked as inferred or legacy. Catalog membership can change. Endpoint selection for documented models follows [OpenCode Go's model and endpoint tables](https://opencode.ai/docs/go/); for unlisted IDs, Hermes's provider prefix router is evidence of the client API mode, not evidence of the vendor's effort semantics.

## OpenCode Go catalog snapshot and current docs (44 IDs)

“Inject” means the current plugin has a positively supported exact route for adding a missing control. “Rewrite” means the route has positive control evidence, but is not enabled for insertion in the current registry. “No-op if absent” means no positive evidence justifies inventing an effort field; it does not mean the vendor has rejected that field. The operator override described below can opt a model into generic known carriers, but does not change these evidence labels. All these routes use the `opencode-go` provider family. The current registry's writable containers are top-level `reasoning.effort` for Responses, top-level `reasoning_effort` for Chat Completions, and `reasoning_effort` plus `extra_body.thinking.type="enabled"` for paired routes.

| Exact Go model ID | API mode | Compatibility / effort evidence |
|---|---|---|
| `grok-4.7` | `codex_responses` | **Inject:** `reasoning.effort`, low/medium/high/xhigh. [xAI documents](https://docs.x.ai/developers/model-capabilities/text/reasoning) all four levels and no disabled setting; the Go endpoint table maps Grok 4.7 to Responses. |
| `grok-4.6` | `codex_responses` | **Inject:** `reasoning.effort`, low/medium/high/xhigh; xAI documents the same control. |
| `grok-4.5` | `codex_responses` | **Inject:** `reasoning.effort`, low/medium/high; xAI documents this control, with xhigh treated as high. This catalog ID is not in the current Go docs list; mode is inferred from Hermes's `grok-*` route. |
| `gpt-6-luna` | `codex_responses` | **Inject:** the plugin conservatively emits low/medium/high/xhigh, excluding `max`. OpenCode's endpoint table selects Responses. Hermes model metadata has a broader vocabulary; the plugin does not infer that broader set as route-specific vendor evidence. |
| `gpt-5.6-luna` | `codex_responses` | **Inject:** `reasoning.effort`, low/medium/high/xhigh/max. OpenCode's endpoint table selects Responses; Hermes Codex model metadata supplies the supported effort vocabulary. |
| `muse-spark-1.3-contributor` | `codex_responses` | **Inject:** `reasoning.effort`, minimal/low/medium/high/xhigh. Exact Contributor variant; max is excluded. OpenCode's endpoint table selects Responses; [Meta reasoning docs](https://dev.meta.ai/docs/reasoning) distinguish Contributor effort levels. |
| `muse-spark-1.2-contributor` | `codex_responses` | **Inject:** `reasoning.effort`, minimal/low/medium/high/xhigh. Exact Contributor variant; max is excluded. |
| `glm-5.3-flash` | `chat_completions` | **No-op if absent in the current registry:** [Z.ai documents low/high/max](https://docs.z.ai/guides/overview/concept-param), but this exact Go route is not enabled for field insertion. The profile records lab behavior; it does not authorize injection. |
| `glm-5.3` | `chat_completions` | **Inject:** top-level `reasoning_effort` low/high/max plus `extra_body.thinking.type="enabled"`. [Z.ai's GLM-5.3 announcement](https://z.ai/blog/glm-5.3) documents effort levels and the thinking control. |
| `glm-5.2` | `chat_completions` | **Inject:** top-level `reasoning_effort`, high/max only. The Hermes Go profile emits this field for this exact family; [Z.ai's GLM-5.2 announcement](https://z.ai/blog/glm-5.2) documents High/Max. Low and medium clamp to high. |
| `glm-5.1` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no exact effort evidence. |
| `glm-5` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no exact effort evidence. |
| `kimi-k3` | `chat_completions` | **Inject:** top-level `reasoning_effort`, low/high/max. [Kimi's model selection docs](https://www.kimi.ai/help/kimi-api/api-model-selection) state K3 always reasons and accepts these values. For legacy label clamping, medium maps to high and xhigh maps to max. Native named choices do not offer xhigh on this route; when a named xhigh decision moves here, it clamps to high. |
| `kimi-k2.7-code` | `chat_completions` | **No-op if absent:** Go profile has a Kimi K2 thinking toggle, but no verified model-specific effort vocabulary was found. |
| `kimi-k2.6` | `chat_completions` | **No-op if absent:** Kimi documents switching thinking on/off, not an effort-level control. |
| `kimi-k2.5` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no exact effort evidence. |
| `longcat-2.0` | `chat_completions` | **Inject since 2026-11-16:** [LongCat documents a thinking toggle](https://longcat.chat/platform/docs/api/chat.html), not discrete effort levels, but the exact Go route accepted top-level `reasoning_effort` at low/medium/high/xhigh/max in one live POST per level (note 9, acceptance only). |
| `longcat-2.5-preview-free` | `chat_completions` | **No-op if absent:** LongCat documents a thinking toggle, not discrete effort levels; the Go free alias's field acceptance is unverified. |
| `deepseek-v4.1-flash` | `chat_completions` | **Inject:** paired top-level `reasoning_effort` plus `extra_body.thinking.type="enabled"`; this exact Go route allows low/medium/high/max. DeepSeek documents the paired control, and a prior live request completed with `reasoning_effort: medium`. |
| `deepseek-v4-pro` | `chat_completions` | **Inject:** paired top-level `reasoning_effort` plus `extra_body.thinking.type="enabled"`; current vocabulary low/high/max. [DeepSeek thinking-mode docs](https://api-docs.deepseek.com/guides/thinking_mode/) document the paired request control. |
| `deepseek-v4-flash` | `chat_completions` | **Inject:** same paired control and current vocabulary low/high/max. |
| `deepseek-v4-flash-vision-exp` | `chat_completions` | **No-op if absent in current registry; positive vendor control exists.** [DeepSeek says this legacy ID temporarily routes to V4.1-Flash](https://api-docs.deepseek.com/news/news260910/), whose documented values are low/high/max; this exact route is not in the plugin's insertion table. |
| `deepseek-flash` | inferred `chat_completions` | **No-op if absent:** DeepSeek's [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/) documents the paired `reasoning_effort` and `thinking.type="enabled"` controls for its `deepseek-flash` API model. The legacy OpenCode Go catalog slug is outside the injection registry; Go-proxy mapping and acceptance are unverified. |
| `mimo-v2.6-flash` | `chat_completions` | **Inject:** top-level `reasoning_effort`, low/medium/high, on this exact Go route. The [dsh-opencode-go maintainer's direct Go probes](https://github.com/Duskriver/dsh-opencode-go/blob/6a834cfda0f4db4e08243b40883eec71abddf655/docs/verification.md#issue-28-mimo-reasoning-controls-2026-10-02) returned HTTP 200 for all three values, despite [Go's catalog entry](https://github.com/anomalyco/models.dev/blob/dev/providers/opencode-go/models/mimo-v2.6-flash.toml) declaring `reasoning_options = []`. [Xiaomi's Responses API](https://mimo.mi.com/docs/en-US/api/chat/responses) says all non-`none` values enable reasoning with identical behavior; graded intensity and cache/cost savings have not been measured. `none` is never chosen automatically. |
| `mimo-v2.6-pro` | `chat_completions` | **No-op if absent:** same thinking toggle; no discrete effort enum is documented. |
| `mimo-v2.5` | `chat_completions` | **No-op if absent:** same thinking toggle; no discrete effort enum is documented. |
| `mimo-v2.5-pro` | `chat_completions` | **No-op if absent:** same thinking toggle; no discrete effort enum is documented. |
| `mimo-v2-pro` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no exact effort evidence. |
| `mimo-v2-omni` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no exact effort evidence. |
| `minimax-m3` | `anthropic_messages` | **No-op if absent:** MiniMax documents an adaptive thinking toggle, not discrete effort for M3; OpenCode uses Anthropic Messages, and no effort-field injection is authorized. `auto` retains a route decision because this path is not registered for dynamic effort updates. |
| `minimax-m2.7` | `anthropic_messages` | **No-op if absent:** MiniMax says M2.7 cannot disable thinking and documents no discrete effort values; OpenCode uses Anthropic Messages. |
| `minimax-m2.5` | inferred `anthropic_messages` | **No-op if absent:** catalog-only ID; route inferred from Hermes prefix; no effort evidence. |
| `qwen3.8-max` | `anthropic_messages` | **No-op if absent:** Alibaba documents `reasoning.effort` on its Responses API, but OpenCode Go uses Anthropic Messages; that does not authorize injection on this route. |
| `qwen3.8-flash` | `anthropic_messages` | **No-op if absent:** same route boundary; the catalog profile summarizes Alibaba's levels but does not prove Go accepts a field. |
| `qwen3.7-plus` | `anthropic_messages` | **No-op if absent:** Alibaba documents thinking and effort controls on its own API; OpenCode Go's Anthropic route has no verified writable effort field. |
| `qwen3.7-max` | inferred `anthropic_messages` | **No-op if absent:** catalog-only ID; route inferred from Hermes prefix; no effort evidence. |
| `qwen3.6-plus` | inferred `anthropic_messages` | **No-op if absent:** catalog-only ID; no effort evidence. |
| `qwen3.5-plus` | inferred `anthropic_messages` | **No-op if absent:** catalog-only ID; no effort evidence. |
| `hy4-preview` | `chat_completions` | **No-op if absent:** Tencent lists deep-thinking support but no discrete effort vocabulary; Go route field acceptance is unverified. |
| `hy3` | `chat_completions` | **No-op if absent:** Tencent lists deep-thinking support but no discrete effort vocabulary; Go route field acceptance is unverified. |
| `hy3-preview` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no effort evidence. |
| `space-bunny` | `chat_completions` | **No-op if absent:** current Go docs list this ID, but the upstream lab and its effort-field documentation are not public. |
| `space-bunny-free` | `chat_completions` | **Legacy no-op if absent:** this ID appeared in the 2026-10-05 model endpoint snapshot, but not the current docs list. A prior live run completed successfully without an effort field and made zero scorer probes. |
| `omen-alpha` | inferred `chat_completions` | **No-op if absent:** catalog-only ID; no exact route or effort evidence. |

## Other tested routes and coverage boundaries

One table, one row per exact route family the runtime can rewrite. Every row is checkable at the
symbol in its **Source** column; a row that is not settled by code or by a dated run says so
instead of guessing.

**Legend**

- **Named vocabulary** — the values the scorer may choose from on that exact route. `numeric 0..2`
  is the legacy rubric score mapped to `low` (< 0.5) / `medium` (< 1.5) / `high`.
- **Carrier field** — the field the plugin rewrites. `paired_effort` means top-level
  `reasoning_effort` that is only writable while `extra_body.thinking.type="enabled"` is present.
- **Injection** — *may add* means missing-field injection is authorized for this exact route;
  *existing field only* means the plugin never invents the field and fails open with
  `unsupported` when it is absent.
- **Dynamic** — may `auto`/`always` change effort every turn? *yes* = exact listed evidence plus
  transport cache-safety; *no (pinned)* = one decision per route, re-clamped on a route change;
  *trial-only* = dynamic only after the route trial's own verdict. A pinned route keeps that
  verdict until the code or the evidence changes; nothing here is a claim that changing effort is
  free.
- **Cache evidence** — what was measured, with its date. *unmeasured* is not *safe*.

For `opencode-go` / `chat_completions`, the 2026-11-14 matrix measured that the effort value partitions the prefix cache — one cold miss per newly used value, and prefix size only sets the size of that miss — in [`opencode-go-cache-20261114`](validation/runs/opencode-go-cache-20261114/report.md). `adaptive_effort_routes` is the operator's opt-in back to per-turn effort on such a pinned route, and it changes dynamic status only, never what may be written.

| Provider / API mode | Exact models | Carrier field | Named vocabulary | Injection | Dynamic | Cache evidence | Source |
|---|---|---|---|---|---|---|---|
| Any other route (Jev, OpenAI Decisions, or any scorer) | — | existing `extra_body.reasoning.effort`, top-level `reasoning_effort`, or top-level `reasoning.effort` | numeric 0..2 | existing field only | trial-only | unmeasured | `effort.score_to_label`, `middleware._effort_slot`, `route_trial.eligible_route` |
| `opencode-go` / `codex_responses` | `gpt-6-luna`, `gpt-5.6-luna`, `grok-4.7`, `grok-4.6`, `grok-4.5`, `muse-spark-1.3-contributor`, `muse-spark-1.2-contributor` | top-level `reasoning.effort` | `gpt-6-luna`, `grok-4.7`, `grok-4.6`: low/medium/high/xhigh; `gpt-5.6-luna`: + max; `grok-4.5`: low/medium/high; Muse contributors: minimal/low/medium/high/xhigh | may add | yes | unmeasured (the 2026-11-14 Go probe covered `chat_completions` only) | `effort.OPEN_CODE_GO_INJECTION_ROUTES["codex_responses"]` |
| `opencode-go` / `chat_completions` | `glm-5.2`, `glm-5.3`, `glm-5.3-flash`, `kimi-k3`, `mimo-v2.6-flash`, `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-v4.1-flash`, `minimax-m3.1-flash`, `step-5-preview`, `hy4-flash-free`, `qwen4-max`, `kimi-k2.7-code`, `longcat-2.0`, `iceman-contributor-free` | `reasoning_effort`; `paired_effort` for `glm-5.3` and the three `deepseek-*` | `glm-5.2`: high/max; `glm-5.3`, `glm-5.3-flash`, `kimi-k3`, `deepseek-v4-pro`, `deepseek-v4-flash`: low/high/max; `deepseek-v4.1-flash`: low/medium/high/max; `mimo-v2.6-flash`, `step-5-preview`: low/medium/high; `minimax-m3.1-flash`, `qwen4-max`, `kimi-k2.7-code`, `longcat-2.0`: low/medium/high/xhigh/max; `hy4-flash-free`: minimal/low/medium/high/xhigh/max; `iceman-contributor-free`: low/medium/high/xhigh | may add (paired routes need `thinking.type="enabled"` already present) | no (pinned, 2026-11-14) | 2026-11-14: `deepseek-v4.1-flash`, 2.1k and 7.6k prefixes - first use of a new value misses, then hits; a delay does not rescue it (note 4). 2026-11-16: five new ids, one 200 per listed level, acceptance only (note 9) | `effort.OPEN_CODE_GO_INJECTION_ROUTES["chat_completions"]`, `middleware._dynamic_effort_route` |
| `opencode-zen` (also `opencode`, `zen`) / `codex_responses` | `muse-spark-1.3`, `muse-spark-1.2`, `muse-spark-1.3-contributor-free` | top-level `reasoning.effort` | `1.3`: minimal/low/medium/high/xhigh/max; `1.2` and `1.3-contributor-free`: minimal/low/medium/high/xhigh | may add | yes | unmeasured | `effort.MUSE_INJECTION_EFFORTS` |
| `openai-codex` / `codex_responses` | `gpt-6.1-sol`, `gpt-6.1-sol-900k`, `gpt-6-astra`, `gpt-6-astra-900k`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-sol-900k`, `gpt-6-luna`, `gpt-6-luna-900k`, `gpt-5.6-sol`, `gpt-5.6-sol-900k`, `gpt-5.6-terra`, `gpt-5.6-terra-900k`, `gpt-5.6-luna`, `gpt-5.6-luna-900k` | top-level `reasoning.effort` | host `codex_supported_efforts` filtered to the choice names: low/medium/high/xhigh/max, `gpt-6.1-terra` low/medium/high/xhigh; `none` is never offered (note 3) | existing field only | yes for `gpt-6.1-sol` only; no (pinned) for the other fourteen | transport only (`prompt_cache_key` identical for low and high); per-level unmeasured | `effort.CODEX_CHOICE_MODELS` |
| `anthropic` / `anthropic_messages`, per-message | `claude-fable-5-1`, `claude-mythos-5-1`, `claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5-5` | `output_config.effort` (per-message marker) | low/medium/high/xhigh/max | may add `output_config.effort` | yes (guarded, note 5) | 2026-10-29 history probe (note 1) | `effort.ANTHROPIC_EFFORT_ROUTES`, `effort.ANTHROPIC_PER_MESSAGE_MODELS` |
| `anthropic` / `anthropic_messages`, top-level | `claude-fable-5`, `claude-mythos-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-5`, `claude-sonnet-4-6` | `output_config.effort` | low/medium/high/xhigh/max; `claude-opus-4-6` and `claude-sonnet-4-6` low/medium/high/max | may add `output_config.effort` | no (pinned) | unmeasured | `effort.ANTHROPIC_EFFORT_ROUTES` |
| `openrouter` / `chat_completions`, Claude | `anthropic/claude-opus-5.5`, `anthropic/claude-sonnet-5.5`, `anthropic/claude-haiku-5.5`, `anthropic/claude-fable-5.1`, `anthropic/claude-opus-5`, `anthropic/claude-sonnet-5`, `anthropic/claude-fable-5` | top-level `verbosity` | low/medium/high/xhigh/max | existing field only | no (pinned) | 2026-10-29 matrix (note 2) | `effort.OPENROUTER_CLAUDE_VERBOSITY_ROUTES` |
| `openrouter` / `chat_completions`, Argon | `google/gemini-4-argon` | existing `extra_body.reasoning.effort` | low/medium/high | existing field only | no (pinned) | 2026-10-29: 0/0 reasoning tokens at low, 2,183/2,055 at high; cache probe inconclusive | `effort.OPENROUTER_CHOICE_ROUTES` |
| `claude-subscription-directsdk-experimental` / `chat_completions` | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-4-8`, `claude-sonnet-5`, each also with the exact `[1m]` suffix (10 IDs) | existing `extra_body.reasoning.effort` (the adapter maps it to `output_config.effort` / `--effort`) | low/medium/high/xhigh/max | existing field only | no (pinned) | 2026-10-29 acceptance only (note 5); a per-change replay was measured, not re-run here (note 1) | `effort.DIRECTSDK_CHOICE_ROUTES` |
| Gemini native (`gemini`, `google`, `google-gemini`, `google-ai-studio`) / `chat_completions` | `gemini-4-argon`, `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3-flash-preview` at low/medium/high; `gemini-3.1-pro-preview` at low/high | `extra_body.thinking_config.thinkingLevel` (or `thinking_level`, key style kept) | low/medium/high, except `gemini-3.1-pro-preview`: low/high | existing field only | no (pinned) | 2026-10-29: raw `minimal`/`xhigh` returned HTTP 400; the cache probe omitted the counter, so reuse is unmeasured | `effort.GEMINI_NATIVE_THINKING_ROUTES` |
| Gemini, OpenAI-compatible (same providers and models) / `chat_completions` | same seven IDs | `extra_body.extra_body.google.thinking_config.thinking_level` (snake_case only) | same as the native row | existing field only | no (pinned) | unmeasured | `effort.gemini_openai_thinking_route` |
| `commandcode` / `chat_completions` | `deepseek/deepseek-v4.1-flash` | top-level `reasoning_effort` | low/high/max; normalized `medium` maps to `high` | existing field only | no (pinned) | unmeasured | `effort.COMMANDCODE_CHOICE_ROUTES` |
| `effort_models` operator assertion | any exact ID the operator names | `reasoning.effort` (`codex_responses`) or top-level `reasoning_effort` (`chat_completions`) | numeric 0..2 (no vocabulary is granted) | may add, on those two carriers only | trial-only | unmeasured | `middleware._injection_path`, `settings.effort_models` |

Dated evidence for these cells lives in [`docs/validation/runs/`](validation/runs/): the
OpenCode Go cache runs (`opencode-go-20261111`, `opencode-go-cache-20261114`), the 2026-11-11 Go
vocabulary probe, and the 2026-10-07 / 2026-10-21 live and rubric campaigns.

### Evidence notes

1. **Anthropic and DirectSDK history (2026-10-29).** A controlled history probe found Sonnet 5.5
   plain-text history reused at 99.8% across effort changes; Opus 5.5 read an older extent after a
   signed thinking block; first changes to a level not recently used read about 49,227 of
   266k–348k prompt tokens in production. These are operator measurements, not a run directory in
   this worktree, so treat them as reported rather than reproducible here.
2. **OpenRouter matrix (issue #101, 2026-10-29).** 110 requests across 11 Claude base models
   completed; seven newer models reported reasoning-token differences and four 4.x models reported
   zero at every level. Opus 5.5 showed cache retention in text-only growing-history probes, which
   is not treated as dynamic-route proof. The 4.x models, `:batch`, aliases and lookalike hosts are
   deliberately excluded.
3. **Codex ladders.** Four registered IDs (`gpt-6.1-sol`, `gpt-6.1-sol-900k`, `gpt-6-astra`,
   `gpt-6-astra-900k`) sit on a ladder that declares no `none` at all; `gpt-6.1-terra` stops at
   `xhigh`; every other registered ID reaches `max` and its ladder declares `none`, which is
   filtered out and never offered.
4. **OpenCode Go `chat_completions` pin (2026-11-14).** The two runs in
   [`opencode-go-cache-20261114`](validation/runs/opencode-go-cache-20261114/report.md) measured
   `deepseek-v4.1-flash` at 2.1k and 7.6k prompt tokens: the first use of a new value misses and
   the next call at it hits (3/3 small-prefix repetitions, 3/3 large-prefix `low → max` runs), the
   lost block grows with the prefix size, and a 10 s or 30 s delay never converts the post-change
   miss into a hit while the same-effort control recovers. The speculative-cause section of that
   report (why a miss follows a change) is superseded by its own matrix run; the pin is a
   precaution on unproven cost, not a verdict on the provider. Production replays the operator
   observed are not recorded in this tree, so that rate is unverified here.
5. **Acceptance is not intensity.** Claude Code 2.1.308 accepted all five levels on the `[1m]`
   variants; Fable 5.1 and Opus 5 showed fallbacks, Haiku 4.5 does not support effort and Daybreak
   is not registered. The per-message Anthropic path additionally needs HTTPS `api.anthropic.com`,
   a mutable existing `anthropic-beta` header (the plugin appends
   `mid-conversation-output-config-2026-07-01` without dropping existing values), a Messages
   request and no `thinking.type="between_tools"`; the top-level initial effort stays unchanged.
6. **Scorer formats are a separate question from routes.** Jev/System One and OpenAI Decisions
   expose named-choice question formats, described in the
   [TypeSafe Choice guide](https://docs.typesafe.ai/primitives/choice) and
   [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions). The local
   suite verifies request shapes and strict parsing; it does not establish live scorer acceptance
   or answer quality.
7. **Vendor documentation and local tests.** The catalogs and parameter guides reviewed on
   2026-10-07 (Z.ai, Kimi, DeepSeek, xAI, Meta, LongCat, MiniMax, Alibaba, Tencent) are listed in
   *Primary references*; documentation alone never broadens the registry. `tests/test_effort.py`
   checks the route vocabulary tables and `tests/test_middleware.py` checks request mutation,
   route eligibility and fail-open behaviour. Neither sends a completion request, so a row here
   means "this plugin will write this field on this exact route", never "the vendor applied it".
8. **One dated live observation.** The 2026-10-03 evaluation recorded Codex `gpt-6.1-sol` sending
   `reasoning.effort: low` and Go `deepseek-v4.1-flash` sending `reasoning_effort: medium`; both
   conversations completed. A Space Bunny Free request completed with no effort field and made
   zero scorer probes. Individual observations, not model validation — see
   [the narrative report](reviews/live-iris-use-cases-20261003.md).
9. **OpenCode Go catalogue run, 2026-11-16.** Five ids were registered from it (`hy4-flash-free`,
   `qwen4-max`, `kimi-k2.7-code`, `longcat-2.0`, `iceman-contributor-free`): one live POST per
   listed level returned 200 on `reasoning_effort`, and `iceman-contributor-free` refused `max`
   with the gateway's own supported list. Nine ids returned HTTP 403 "Model access is disabled"
   and `longcat-2.5-free` returned 429 on every attempt, so all ten are deliberately absent from
   the registry: no access is not evidence about a field, and absence here is not a claim that
   the model lacks one. Acceptance only — intensity, cache, cost and quality are unmeasured.

### Operator force list for unverified model IDs

`effort_models` lets an operator assert support for exact IDs on known Responses/Chat Completions carriers. It does not verify vendor acceptance or grant dynamic capability. Existing route vocabularies and disabled/malformed-control guards still apply. Keep the list narrow: a downstream provider can reject an assumed carrier. See [configuration and matching rules](CONFIGURATION.md#assert-support-for-an-exact-model) and the [runtime contract](CONTRACTS.md#writable-controls-and-route-vocabulary).

## Primary references

- [OpenCode Go models, endpoints, and validated-client notes](https://dev.opencode.ai/docs/go/)
- [xAI reasoning effort documentation](https://docs.x.ai/developers/model-capabilities/text/reasoning)
- [Kimi API model selection](https://www.kimi.ai/help/kimi-api/api-model-selection)
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [Z.ai GLM-5.2 announcement](https://z.ai/blog/glm-5.2)
- [Z.ai GLM-5.3 announcement](https://z.ai/blog/glm-5.3)
- [Meta reasoning documentation](https://dev.meta.ai/docs/reasoning)
- [LongCat API thinking controls](https://longcat.chat/platform/docs/api/chat.html)
- [Xiaomi MiMo Anthropic API](https://mimo.mi.com/docs/en-US/api/chat/anthropic-api)
- [MiniMax Anthropic API](https://platform.minimax.io/docs/api-reference/text-anthropic-api)
- [Alibaba Qwen Responses API](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-responses)
- [Tencent TokenHub model list](https://cloud.tencent.com/document/product/1823/130051)
- [DeepSeek model list](https://api-docs.deepseek.com/api/list-models/)
- [DeepSeek V4.1-Flash release and legacy aliases](https://api-docs.deepseek.com/news/news260910/)
