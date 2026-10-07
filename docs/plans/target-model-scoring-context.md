# Target model context for effort scoring

Implemented design record. The opt-in context path and expanded catalog are part of source commit `e1d7e99`, confirmed on GitHub on 2026-10-07. Use [Configuration](../CONFIGURATION.md#guide-classification) for current settings and [Development](../DEVELOPMENT.md#adding-target-model-profiles) for catalog maintenance.

The notes below preserve the 2026-10-06/07 implementation checkpoint: the initial context path and later catalog expansion, including the dated OpenRouter and OpenCode Go snapshots. Test statements describe that checkpoint; they are not a fresh verification of the current tree. No live scorer or answer-quality evaluation is established by this record.

## Goal and recommended approach

Let any selected scorer use the target provider, exact model, API mode, and observed request effort, supplemented by a compact summary of that model's documented effort semantics.

Start by delivering this information as bounded context alongside the task. Retain the existing finite `0..2` score contract, thresholds, and deterministic route clamp. Evaluate the contextual input before considering model-specific scoring thresholds or changes to the selectable labels.

Documentation describes controls and vendor recommendations; it does not establish the optimal effort for this plugin's tasks. Current effort is a baseline observation, not a desired answer or evidence of task difficulty.

## Source facts and their consequences

| Source | Verified fact | Design consequence |
| --- | --- | --- |
| [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning?api-mode=responses) | Accepted levels and defaults depend on the model. GPT-6.1 Sol defaults to medium and excludes none/minimal. | Bind documentation to an exact model and route; distinguish a documented default from the value actually present in the request. |
| [Claude effort guide](https://platform.claude.com/docs/en/build-with-claude/effort) | Effort affects generated text, tool calls, and thinking when active. It operates even without thinking enabled. | Avoid describing all vendors' effort as a reasoning-token budget. A documentation profile does not authorize a new Hermes transport control. |
| [DeepSeek Responses reference](https://api-docs.deepseek.com/api/create-response/) | Its Responses effort parameter controls thinking enablement as well as intensity; none disables thinking. | Record the protocol-specific relationship between effort and thinking. Preserve the plugin's disabled-control guards. |
| [Google Gemini thinking guide](https://ai.google.dev/gemini-api/docs/thinking) | Supported levels and defaults differ by exact model; minimal does not guarantee that thinking is fully off. | Keep Gemini profiles exact and do not describe minimal as a thinking toggle. |
| [xAI reasoning guide](https://docs.x.ai/developers/model-capabilities/text/reasoning) | Grok 4.5 supports low/medium/high; xhigh is available on 4.6 and later. Grok 4.20 multi-agent effort selects agent count, not reasoning depth. | Split profiles by model version and control semantics; do not equate similarly named effort fields across models. |
| [Cohere reasoning guide](https://docs.cohere.com/docs/reasoning) | Command A Reasoning enables thinking by default and supports a separate thinking-token budget. Its compatibility API maps reasoning_effort to none/high. | Describe the toggle/budget without implying graded levels or a default token budget. |
| [Mistral reasoning guide](https://docs.mistral.ai/studio/conversations/reasoning) | Mistral Small Latest, Medium 3.5, and Large 4.0 support adjustable reasoning_effort; high adds a thinking chunk and token use. | Record only model-specific documented behavior and leave the default unknown. |
| [Z.ai core parameters](https://docs.z.ai/guides/overview/concept-param) | GLM-5.2 has seven documented effort values with aliases; GLM-5.3 and GLM-5.3-Flash support low/high/max; GLM-4.5+ also has thinking controls. | Keep effort-level differences and thinking-only models in separate exact-ID profiles. |
| [Kimi thinking-model guide](https://platform.kimi.ai/docs/guide/use-thinking-models) | K3 supports low/high/max; K2.7 Code always thinks; K2.6 thinking is enabled by default and can be disabled. | Describe each model's actual control rather than treating every thinking model as having an effort enum. |
| [OpenRouter free variants](https://openrouter.ai/models?variant=free) and [Free Models Router](https://openrouter.ai/openrouter/free) | The current listing and router page report 23 free variants; `openrouter/free` selects among free models dynamically. | Keep a dated exact-ID snapshot plus a separate dynamic-router profile; never assume the router's selected model in advance. |
| [OpenCode Go current model list](https://opencode.ai/docs/go/) | Its current 30-model roster includes the covered LongCat, MiMo, MiniMax, Muse Contributor, Qwen, DeepSeek, Tencent Hy, GLM, Kimi, Grok, GPT, and Space Bunny IDs. Space Bunny's upstream lab is undisclosed. | Match each provider-specific ID exactly; for Space Bunny cite the router's official listing and leave effort values empty. |
| [LongCat API guide](https://longcat.chat/platform/docs/OpenCode.html) and [Xiaomi MiMo API guide](https://mimo.mi.com/docs/en-US/api/chat/anthropic-api) | LongCat exposes a thinking toggle; MiMo's listed Go models use thinking enabled/disabled, enabled by default. Neither guide lists graded effort levels. | Summarize toggles without fabricating discrete levels. |
| [MiniMax Anthropic API guide](https://platform.minimax.io/docs/api-reference/text-anthropic-api) | M3 uses a thinking toggle; M2.7 cannot disable thinking. Discrete effort is currently documented for another model, not these Go IDs. | Keep M3/M2.7 profiles toggle-only. |
| [Meta reasoning guide](https://dev.meta.ai/docs/reasoning) | Muse Contributor accepts minimal through xhigh; none is rejected and max is restricted to standard 1.3. | Keep Go Contributor IDs separate from standard Muse profiles. |
| [Alibaba Qwen Responses guide](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-responses) | Qwen3.8 Max/Flash list low/medium/xhigh, default xhigh, with mappings for common OpenAI effort aliases. Qwen3.7 Plus has no model-specific accepted-level set in the cited guide. | Record Qwen3.8's levels, and keep Qwen3.7 Plus values unspecified. |
| [Tencent TokenHub model list](https://cloud.tencent.com/document/product/1823/130051) | Hy3 and Hy4 Preview are marked as deep-thinking models; the list gives no discrete effort levels. | Describe the reasoning capability without claiming an effort enum. |
| [DeepSeek model list](https://api-docs.deepseek.com/api/list-models/) | V4.1 Flash uses low/high/max with high default; old V4 Flash and Vision Exp IDs temporarily route to it. | Attach the current semantics to the exact legacy IDs listed by OpenCode Go. |

DeepSeek's current [model list](https://api-docs.deepseek.com/api/list-models/) and [thinking guide](https://api-docs.deepseek.com/guides/thinking_mode/) were checked before adding the Go aliases. DeepSeek now temporarily routes its legacy V4 Flash and Vision Exp IDs to V4.1 Flash; profile metadata describes the lab's current model behavior, while the separate OpenCode compatibility matrix continues to control injection.

## Proposed configuration and data contract

The implementation adds one opt-in boolean, `use_target_model_context`, default false. It controls scorer enrichment only; the public routing modes remain auto, once, always, and off. Enabling it shares target route identity and observed effort with the selected scorer.

Keep reviewed profiles in a local data catalog with no runtime network I/O. Each profile records its vendor, exact model IDs, source URL, review date, documented levels/default, and a short description of effort semantics. Group IDs only when those facts all match. No substring-based capability inference.

Build a per-request context from:

- Target provider, exact model, and API mode from the middleware's route identity.
- Observed effort from the same validated field that will be rewritten, captured before any rewrite; represent an absent field as absent.
- The applicable reviewed documentation summary, if present.
- A low/medium/high-to-wire mapping only where the route already has positive support evidence. The generic fallback vocabulary must not be described as verified model support.

Keep model-scorer identity distinct from target-model identity. For example, a Jev scorer can receive context describing a GPT target; that context must not change Jev's configured model or endpoint.

Unknown profiles use the existing generic rubric without invented defaults or model recommendations. Malformed, disabled, or none reasoning controls retain the current early return and make no scorer call. Context-building exceptions follow the existing failure path and leave the original request unchanged.

## Implementation sequence

1. **Profile catalog implemented.** `model_profiles.py` validates `model_profiles.json`, indexes exact model IDs, and serializes bounded target context. The expanded catalog has 76 profiles for 121 exact IDs. It includes all 23 free variants on OpenRouter's 2026-10-07 listing, a separate `openrouter/free` profile, GLM/Kimi families, and exact IDs for the 30-model OpenCode Go docs roster alongside OpenAI, Anthropic, DeepSeek, Google, xAI, Cohere, Mistral, LongCat, MiMo, MiniMax, Muse Contributor, Qwen, and Tencent Hy. Space Bunny uses OpenCode's own listing because its upstream lab is undisclosed. Empty `effort_levels` represents a documented thinking toggle, a non-generative task, or the absence of documented discrete values; it does not imply route support or lack of support. Every row includes a source URL and review date. Runtime requests never fetch documentation.

2. **Shared input preparation implemented.** Middleware truncates the task first with the existing `prompt_chars` policy, then prepends a JSON-escaped context capped at 1,400 characters. Existing adapter input caps are set to the combined prompt length for this call only, so no provider gets less task text because context was enabled. The guidance cap remains independent. All adapters continue using their current request format.

3. **Scorer transport coverage implemented.** The common composed string reaches Jev, Cloudflare, custom System One, OpenRouter, custom Chat Completions, and OpenAI Decisions in each adapter's existing user-input field. There are no provider-specific prompt branches or extra calls.

4. **Decision-point integration implemented.** `_handle` builds context only after the route and writable effort slot are eligible and a scoring claim is acquired. Current effort is not a memo key and never triggers reclassification. Plain `/hae probe` remains text-only plus configured guidance.

5. **Contracts and documentation updated.** The initial feature tests cover catalog growth through data-only edits, exact matching, serialization/bounds, scorer transport formats, opt-in behavior, probe isolation, settings parity, fail-open context errors, and rubric anti-anchoring language. README, CONTRACTS, HANDOFF, and DEVELOPMENT document the sharing boundary and profile maintenance. The initial 503-case network-free suite and Ruff run passed before the 2026-10-07 catalog expansion; that expansion has not been re-run. No live scorer or answer-quality evaluation was run.

## Scorer input shape

Conceptually, all adapters receive:

```text
TARGET CONTEXT
provider/model/API identity
observed effort: value or absent
reviewed effort semantics and documented recommendations
verified mapping of normalized labels to wire values, when available

TASK
bounded latest user request, or parent-written child goal
```

The fixed rubric still asks for a finite numeric score between 0 and 2. It must state that the context concerns the model answering the task, that current effort does not dictate the result, and that unsupported or omitted documentation must not be guessed.

## Acceptance and evaluation

- With the setting off, existing input behavior is preserved.
- With it on, all built-in scorer transports receive the same substantive context for the same eligible request.
- Setting low versus high as the current effort does not mechanically force the scorer to repeat that value.
- Task length and guidance limits are preserved independently of context size.
- Mode off, unsupported routes, and disabled controls still make zero calls. Errors remain fail-open, without provider fallback.
- One classification per turn, memoized failures, retained route decisions, and route re-clamping retain their current behavior.
- No profiles grant transport support, dynamic-effort status, cache neutrality, or permission to add thinking controls.
- The existing three normalized labels continue to select their existing clamped wire values. This feature alone does not add xhigh/max as independently selectable outcomes.

After deterministic checks, compare task-only scoring, scoring with target identity/current effort, and scoring with the reviewed profile on the same labeled task set. Compare actual final wire values as well as numeric scores, since a route clamp can merge different labels. Repeat selected tasks across several current-effort values to detect anchoring. Measure scorer agreement, unsafe under-allocation, unnecessary escalation, latency, prompt overhead, and failures. To claim better task outcomes or lower total cost, also evaluate the target model's answers and usage; scorer agreement alone is insufficient.

The first version is opt-in. Broader rollout or model-specific calibration follows measured results. Runtime fetching of documentation, automatic profile updates, new Anthropic transport support, and changes to the routing/cache state machine are separate work.
