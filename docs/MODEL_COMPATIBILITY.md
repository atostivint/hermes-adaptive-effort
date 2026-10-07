# Model compatibility

This page separates three kinds of evidence: vendor-documented effort controls, Hermes request routing, and actual completed requests observed with this plugin. A model appearing in the OpenCode Go catalog does not by itself prove that it accepts a reasoning-effort field. Missing-field injection is restricted to exact `(provider, model, api_mode)` routes in `effort.OPEN_CODE_GO_INJECTION_ROUTES`; an unknown route remains a no-op. Existing request fields continue to use the ordinary rewrite path, subject to the route vocabulary and disabled/malformed-control guards.

The OpenCode Go docs list 30 curated model names and provide an endpoint table. A public unauthenticated model-catalog GET at `https://opencode.ai/zen/go/v1/models` returned 43 IDs on 2026-10-05. The table below is the union of that snapshot and the current docs list: 44 exact IDs, including the current `space-bunny` ID and the older `space-bunny-free` snapshot alias. IDs not in the current docs endpoint table are marked as inferred or legacy. Catalog membership can change. Endpoint selection for documented models follows [OpenCode Go's model and endpoint tables](https://opencode.ai/docs/go/); for unlisted IDs, Hermes's provider prefix router is evidence of the client API mode, not evidence of the vendor's effort semantics.

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
| `kimi-k3` | `chat_completions` | **Inject:** top-level `reasoning_effort`, low/high/max. [Kimi's model selection docs](https://www.kimi.ai/help/kimi-api/api-model-selection) state K3 always reasons and accepts these values. Medium maps to high; xhigh maps to max. |
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

OpenCode Zen has three separate positive model routes. Exact model IDs and route vocabularies are explicit in the runtime registry; all use `codex_responses` and top-level `reasoning.effort`:

| Provider | Exact model ID | Wire vocabulary | Evidence status |
|---|---|---|---|
| OpenCode Zen (`opencode-zen` family) | `muse-spark-1.3` | minimal/low/medium/high/xhigh/max | Runtime and unit mapping coverage; Meta docs permit max on standard 1.3. |
| OpenCode Zen (`opencode-zen` family) | `muse-spark-1.2` | minimal/low/medium/high/xhigh | Runtime and unit mapping coverage; max is not included. |
| OpenCode Zen (`opencode-zen` family) | `muse-spark-1.3-contributor-free` | minimal/low/medium/high/xhigh | Runtime and unit mapping coverage for the exact contributor-free slug; this is distinct from the Go catalog's `muse-spark-1.3-contributor`. |

The October 3, 2026 live evaluation exercised Hermes Codex `gpt-6.1-sol` and OpenCode Go `deepseek-v4.1-flash`. Recorded HTTP bodies show Codex `reasoning.effort: low` and Go `reasoning_effort: medium` reaching their Responses and Chat Completions endpoints; both conversations completed. A Space Bunny Free request also completed without an effort field and made zero scorer probes. These are individual route observations, not exhaustive model validation or quality benchmarks. The sanitized narrative report is [available here](reviews/live-iris-use-cases-20261003.md); its linked JSON captures are not copied into this worktree.

Automated Go catalog coverage is narrower than the 44-row inventory: tests exercise exact injection eligibility and no-op behavior for catalog entries, including unsupported models, but do not send completion requests to all 44 IDs. The current `space-bunny` row is a static no-op disposition because that exact ID is absent from the injection allowlist; it has not been live-tested. `tests/test_effort.py` checks the route vocabulary table; `tests/test_middleware.py` checks request mutation, route eligibility, and fail-open behavior. These are local contract tests, not upstream service acceptance tests. A successful response on one exact model should not be generalized to another slug or provider alias.

The separate [OpenCode Muse probe report](reviews/muse-effort-inject-probe.txt) records one Go Responses request for `muse-spark-1.3-contributor` that received HTTP 403 (no completion); it does not validate model acceptance. The report distinguishes that Go slug from `muse-spark-1.3-contributor-free`, for which Go was unverified.

Other local mapping tests cover Hermes Codex effort clamping and the narrow Kimi K3 (`low/high/max`), GLM-5.2 (`high/max`), and GLM-5.3 (`low/high/max`) wire sets. For Kimi K3, normalized `medium` maps to `high`; for GLM-5.3 it also maps to `high`, never to an undocumented `medium`. These tests verify local value mapping and request rewriting, not live acceptance by each vendor endpoint. The plugin's injection allowlist remains separate from this broader rewrite coverage.

The [bounded local Zenon check](reviews/zenon-effort-compatibility-20261005.md) covered two GGUF models from the known local scorer store and found HTTP field acceptance without completed generations, so it makes no support claim. Its additional inventory found no candidate in the checked LM Studio/Ollama locations, `C:/AI`, `C:/LLM`, `C:/models`, or Downloads; W:/X:/Y:/Z: were checked only at top level and not recursively. This is a bounded search, not a whole-machine inventory.

The catalog's OpenCode Go endpoint mapping is a transport compatibility choice, not proof that every model accepts an effort parameter. `auto` uses per-turn decisions only for exact model/API pairs in the dynamic registry and a cache-neutral transport: the documented OpenCode Go/Zen control routes and `openai-codex/gpt-6.1-sol` on Codex Responses. Other routes retain one decision per session/provider/model/API route. `once` always uses that route scope; `always` uses per-turn decisions. Every field addition remains copy-on-write and fail-open. `none`, disabled reasoning, malformed controls, unsupported model aliases, and unknown routes do not trigger field addition.

### Operator force list for unverified model IDs

`effort_models` is an optional comma- or newline-separated string of exact model IDs (default empty). Tokens are trimmed and case-folded; an optional leading provider/namespace prefix is stripped from both configured and request model names, then exact bare IDs are compared across providers. There are no wildcards. This is an explicit operator assertion that the named model accepts the normal effort carrier; it is not vendor verification. The setting permits adding a field in any active mode and only for known `codex_responses` and `chat_completions` containers: Responses uses top-level `reasoning.effort`; Chat Completions uses top-level `reasoning_effort`. It does not apply to Anthropic Messages or unknown modes, and does not establish dynamic support. Existing known route vocabularies still constrain the selected value, and paired routes still require `extra_body.thinking.type="enabled"`; the list does not enable thinking or bypass disabled, malformed, or `none` controls. Keep the configured IDs narrow and exact because a downstream provider can reject an assumed carrier.

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
