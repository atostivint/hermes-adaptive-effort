# Model compatibility

[Configuration](CONFIGURATION.md) · [Runtime contracts](CONTRACTS.md) · [Historical evidence](README.md#historical-evidence)

Registry/source review: 2026-10-07. Catalog entries below are dated evidence, not a live model directory.

This page separates three kinds of evidence: vendor-documented effort controls, Hermes request routing, and actual completed requests observed with this plugin. A model appearing in the OpenCode Go catalog does not by itself prove that it accepts a reasoning-effort field. Missing-field injection is restricted to exact `(provider, model, api_mode)` routes in `effort.OPEN_CODE_GO_INJECTION_ROUTES`; an unknown route remains a no-op. Existing request fields continue to use the ordinary rewrite path, subject to the route vocabulary and disabled/malformed-control guards.

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
| `longcat-2.0` | `chat_completions` | **No-op if absent:** [LongCat documents a thinking toggle](https://longcat.chat/platform/docs/api/chat.html), not discrete effort levels; the Go route's acceptance is unverified. |
| `longcat-2.5-preview-free` | `chat_completions` | **No-op if absent:** LongCat documents a thinking toggle, not discrete effort levels; the Go free alias's field acceptance is unverified. |
| `deepseek-v4.1-flash` | `chat_completions` | **Inject:** paired top-level `reasoning_effort` plus `extra_body.thinking.type="enabled"`; this exact Go route allows low/medium/high/max. DeepSeek documents the paired control, and a prior live request completed with `reasoning_effort: medium`. |
| `deepseek-v4-pro` | `chat_completions` | **Inject:** paired top-level `reasoning_effort` plus `extra_body.thinking.type="enabled"`; current vocabulary low/high/max. [DeepSeek thinking-mode docs](https://api-docs.deepseek.com/guides/thinking_mode/) document the paired request control. |
| `deepseek-v4-flash` | `chat_completions` | **Inject:** same paired control and current vocabulary low/high/max. |
| `deepseek-v4-flash-vision-exp` | `chat_completions` | **No-op if absent in current registry; positive vendor control exists.** [DeepSeek says this legacy ID temporarily routes to V4.1-Flash](https://api-docs.deepseek.com/news/news260910/), whose documented values are low/high/max; this exact route is not in the plugin's insertion table. |
| `deepseek-flash` | inferred `chat_completions` | **No-op if absent:** DeepSeek's [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/) documents the paired `reasoning_effort` and `thinking.type="enabled"` controls for its `deepseek-flash` API model. The legacy OpenCode Go catalog slug is outside the injection registry; Go-proxy mapping and acceptance are unverified. |
| `mimo-v2.6-flash` | `chat_completions` | **No-op if absent:** Xiaomi documents thinking enabled/disabled, not graded effort; Go route acceptance is unverified. |
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

### Native named-choice routes

On these exact routes, the scorer chooses one of the listed wire values directly. This table is a plugin allowlist, not a claim that neighboring IDs, proxies or other API modes behave the same way.

| Provider | Exact model ID(s) | API route | Choice vocabulary | Turn/cache behavior |
|---|---|---|---|---|
| OpenCode Go | `kimi-k3` | `chat_completions` | `low/high/max` | Existing Go route; narrow vocabulary |
| OpenAI Codex | `gpt-6.1-sol` | `codex_responses` | `low/medium/high/xhigh/max` | Dynamic only when transport cache-safety is positive |
| Anthropic native | `claude-fable-5-1`, `claude-mythos-5-1`, `claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5-5` | `anthropic_messages` | `low/medium/high/xhigh/max` | Per-message updates only with an exposed `anthropic-beta` header; plugin appends the documented beta |
| Anthropic native | `claude-fable-5`, `claude-mythos-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-sonnet-5` | `anthropic_messages` | `low/medium/high/xhigh/max` | Top-level effort only; `auto` retains a route decision |
| Anthropic native | `claude-opus-4-6`, `claude-sonnet-4-6` | `anthropic_messages` | `low/medium/high/max` | `xhigh` is excluded; top-level effort only, so `auto` retains a route decision |
| OpenCode Go | Exact IDs in `effort.OPEN_CODE_GO_INJECTION_ROUTES` | Registered route API mode | The route table's exact wire set | Uses the declared choice set; no generic OpenAI-compatible expansion |
| OpenCode Zen | `muse-spark-1.3`, `muse-spark-1.2`, `muse-spark-1.3-contributor-free` | `codex_responses` | Exact Muse tier set in the route table | Uses the exact model tier; Contributor Free excludes `max` |

Anthropic's native field is `output_config.effort`; the per-message path uses an empty system message containing `output_config.effort` immediately before the relevant user turn. The top-level effort remains unchanged. The plugin only activates this path for HTTPS `api.anthropic.com`, the five per-message IDs above, a mutable existing `anthropic-beta` header, and no `thinking.type="between_tools"`. It merges `mid-conversation-output-config-2026-07-01` into that header without removing existing beta values. A proxy, Bedrock route, missing header or incompatible control is not inferred to support it. See Anthropic's [effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort#change-effort-mid-conversation).

OpenAI's route vocabulary follows the registered GPT-6.1 Sol route and Hermes's exact route declaration; see the [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning). Jev/System One and OpenAI Decisions expose named-choice question formats, as described in the [TypeSafe Choice guide](https://docs.typesafe.ai/primitives/choice) and [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions). The local suite verifies request shapes and strict parsing; it does not establish live scorer acceptance or answer quality.

OpenCode Zen has three separate positive model routes. Exact model IDs and route vocabularies are explicit in the runtime registry; all use `codex_responses` and top-level `reasoning.effort`:

| Provider | Exact model ID | Wire vocabulary | Evidence status |
|---|---|---|---|
| OpenCode Zen (`opencode-zen` family) | `muse-spark-1.3` | minimal/low/medium/high/xhigh/max | Runtime and unit mapping coverage; Meta docs permit max on standard 1.3. |
| OpenCode Zen (`opencode-zen` family) | `muse-spark-1.2` | minimal/low/medium/high/xhigh | Runtime and unit mapping coverage; max is not included. |
| OpenCode Zen (`opencode-zen` family) | `muse-spark-1.3-contributor-free` | minimal/low/medium/high/xhigh | Runtime and unit mapping coverage for the exact contributor-free slug; this is distinct from the Go catalog's `muse-spark-1.3-contributor`. |

The October 3, 2026 live evaluation exercised Hermes Codex `gpt-6.1-sol` and OpenCode Go `deepseek-v4.1-flash`. Recorded HTTP bodies show Codex `reasoning.effort: low` and Go `reasoning_effort: medium` reaching their Responses and Chat Completions endpoints; both conversations completed. A Space Bunny Free request also completed without an effort field and made zero scorer probes. These are individual route observations, not exhaustive model validation or quality benchmarks. The sanitized narrative report is [available here](reviews/live-iris-use-cases-20261003.md); its linked JSON captures are not copied into this worktree.

Automated Go catalog coverage is narrower than the 44-row inventory: tests exercise exact injection eligibility and no-op behavior for catalog entries, including unsupported models, but do not send completion requests to all 44 IDs. The current `space-bunny` row is a static no-op disposition because that exact ID is absent from the injection allowlist; it has not been live-tested. `tests/test_effort.py` checks the route vocabulary table; `tests/test_middleware.py` checks request mutation, route eligibility, and fail-open behavior. These are local contract tests, not upstream service acceptance tests. A successful response on one exact model should not be generalized to another slug or provider alias.

The separate [OpenCode Muse probe report](reviews/muse-effort-inject-probe.txt) records one Go Responses request for `muse-spark-1.3-contributor` that received HTTP 403 (no completion); it does not validate model acceptance. The report distinguishes that Go slug from `muse-spark-1.3-contributor-free`, for which Go was unverified.

Local tests cover Hermes Codex clamping and the exact Go registry's Kimi K3 (`low/high/max`), GLM-5.2 (`high/max`) and GLM-5.3 (`low/high/max`) sets. Normalized `medium` maps to `high` for these narrow sets. Outside exact registry entries, Kimi/GLM mapping can use the corresponding Hermes host constants; this does not establish another proxy route's support. These are local mapping/rewrite checks, not live acceptance tests.

The [Z.ai core parameter guide](https://docs.z.ai/guides/overview/concept-param), reviewed on 2026-10-07, documents `low/high/max` for GLM-5.3 and aliases for GLM-5.2. The plugin's exact Go GLM-5.2 route remains the narrower `high/max`. Vendor profiles and proxy wire rules are separate evidence; documentation updates alone do not broaden the registry.

The [bounded local Zenon check](reviews/zenon-effort-compatibility-20261005.md) found HTTP field acceptance without completed generations. It establishes no completed effort-control support; its inventory limits remain in the dated report.

Endpoint mapping establishes a transport choice. Dynamic routing additionally requires an exact registered model/API pair and positive cache-safety evidence. See [decision scope](CONTRACTS.md#decision-scope) and [writable controls](CONTRACTS.md#writable-controls-and-route-vocabulary) for the mode, insertion and preservation rules.

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
