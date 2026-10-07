# Hermes Adaptive Effort

[![CI](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml) [![Security](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml)

A small Hermes plugin that chooses reasoning effort for each user turn, using an external scorer. It changes an existing effort setting and can fill a missing field on exact, documented model routes.

Built for everyday use: one focused job, a quiet interface, and a scorer you can choose. Choose Jev (TypeSafe), OpenAI Decisions, a model you configure through OpenRouter, Cloudflare Clef / Clef Flash, or your own custom scorer endpoint. Jev is the default. The scorer is independent of the model answering the conversation: it only evaluates the task against the rubric and selects an effort level; Hermes still sends the request to your chosen conversation model.

The custom provider can connect to a hosted service or a local model server that implements either System One or OpenAI Chat Completions. Point it at the exact HTTP(S) endpoint and choose the model name your server exposes. This makes the classifier replaceable without changing the conversation model.

An initial Zenon trial loaded Kev 0.8B and 4B through llama.cpp and llama-swap on an RTX 4070 Ti. Across 180 warmed classifications per model on 60 synthetic English and French prompts, agreement with the fixed labels was 50% for 0.8B and 60% for 4B; neither model predicted `high`. The first 4B classification also hit the plugin's 3-second timeout, then all warmed calls succeeded. These exploratory results are not human-gold evaluation or a comparison with Jev. See the [full report](docs/reviews/local-scorer-benchmark-20261005T090911Z.md).

The plugin is opt-in and starts **off**. It does not score or change requests until you choose a routing mode. If scoring fails, your original request continues unchanged. It does not switch your conversation model or add tools. The four modes control when effort is evaluated; existing fields are preferred, and a missing field is added only on an explicitly supported route or an exact model you list in `effort_models`.

## Quick install

Install directly from the GitHub repository root:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort' --enable
```

The plugin manifest and payload now live at the repository root, so no subdirectory fragment is needed. The previous `#hermes-adaptive-effort` suffix selected the old nested payload directory. A root install retains Git metadata, which lets `hermes plugins update hermes-adaptive-effort` update the plugin normally.

Hermes handles installation; you do not need to clone this repo or install a Python package. Enabling the plugin makes it available, while its reasoning mode remains **off** until you opt in.

### 1. Make your scorer key available

Scorer keys are **not** pre-installed: you must provide the key for whichever scorer you select. Keys resolve through Hermes' secret scope (your profile's `.env` file) first, then the process environment. Without the key, every classification fails open with `credential_missing` and your request continues unchanged. Never put keys in `config.yaml`.

#### Where to get each key

| Scorer | Key | Where to get it |
| --- | --- | --- |
| Jev (default) | `TYPESAFE_API_KEY` | From your TypeSafe account — the same key used by the Jev approvals plugin against `https://api.typesafe.ai`. The model defaults to `jev-latest` and can be changed with `jev_model`. |
| OpenAI Decisions | `OPENAI_API_KEY` | From your OpenAI API account. The Decisions API is in public beta; the model defaults to `gpt-6-luna`, and `scorer_model` can override it. |
| OpenRouter | `OPENROUTER_API_KEY` | Create one at [openrouter.ai/keys](https://openrouter.ai/keys). You must also set `scorer_model` to the exact model slug you want to pay for (e.g. a small cheap classifier). The adapter requests ZDR-only routing and refuses data-collecting endpoints. |
| Cloudflare | `CLOUDFLARE_AUTH_TOKEN` + 32-hex account ID | In the Cloudflare dashboard: copy the **Account ID** from the Workers & Pages overview (it is the 32-character hexadecimal `cloudflare_account_id` setting), then create an API token under **My Profile → API Tokens** with Workers AI permission and use it as `CLOUDFLARE_AUTH_TOKEN`. Choose `clef` or `clef-flash` as `cloudflare_model`. |
| Custom (hosted) | `CUSTOM_SCORER_API_KEY` only when `custom_auth: bearer` | From whoever runs the endpoint. The plugin posts to your exact `custom_endpoint` with your `scorer_model`; there is no fallback to another scorer. |
| Custom (local) | No key (`custom_auth: none`) | No key needed — see "No key? Use a local model" below. |

#### Option A — Hermes `.env` file (persistent, recommended)

Add the line to your Hermes home's `.env` file, then restart Hermes so the serving process picks it up:

- Linux / macOS: `~/.hermes/.env`
- Windows: `%LOCALAPPDATA%\hermes\.env`

```text
TYPESAFE_API_KEY=YOUR_KEY
```

One line per scorer if you run several (`OPENAI_API_KEY=…`, `OPENROUTER_API_KEY=…`, `CLOUDFLARE_AUTH_TOKEN=…`, `CUSTOM_SCORER_API_KEY=…`). For a gateway service, use its environment or service `EnvironmentFile` instead — a key exported in your terminal does not reach an already-running service.

#### Option B — shell export (this terminal session only)

For a local session without touching the file:

**Linux / macOS**

```bash
export TYPESAFE_API_KEY="YOUR_KEY"
```

**Windows PowerShell**

```powershell
$env:TYPESAFE_API_KEY = "YOUR_KEY"
```

Set the key before launching Hermes from the same terminal. Substitute the variable name for another scorer.

#### Option C — secrets manager (Bitwarden / 1Password / vault)

Any manager works as long as the value ends up in the Hermes process environment (or `.env`) before launch — the plugin never talks to the manager directly. Examples:

```bash
export TYPESAFE_API_KEY="$(bw get password hermes/TYPESAFE_API_KEY)"
export OPENAI_API_KEY="$(op read 'op://Private/hermes/OPENAI_API_KEY')"
export OPENROUTER_API_KEY="$(op read 'op://Private/hermes/OPENROUTER_API_KEY')"
```

Launch Hermes from that same shell afterwards. If you sync `.env` from a manager, keep the file readable only by you and never commit it.

#### No key? Use a local model (no account, no token)

Select the `custom` scorer and point it at a local OpenAI-compatible server. No key is looked up when `custom_auth: none`:

1. Start a local server that serves either System One or Chat Completions — e.g. `llama-server` directly, or `llama-swap` when you switch between models. Note the exact URL and the model name the server exposes.
2. In the plugin's Desktop settings (or `config.yaml`), set `scorer_provider: custom`, `custom_endpoint` to the **exact** URL (nothing is appended; a tested llama-swap example is `http://127.0.0.1:8099/v1/systemone`), `scorer_model` to the served name (e.g. `effort-kev-08b`), and `custom_api_format` to `systemone` or `chat_completions` to match your server. Keep `custom_auth: none`.
3. Choose a routing mode (`once` to keep one decision per model and route, or `auto` to follow the route's verified capability) — enabling a mode authorizes sending task text to the selected scorer; while the mode stays `off` nothing is sent. Prompts stay on your machine with a local endpoint, but the plugin still only sends text once routing is enabled.

Local-model caveats, measured on one Windows/RTX 4070 Ti box (not a recommendation): Kev 0.8B agreed with the synthetic fixed labels 50% of the time, Kev 4B 60%, and neither predicted `high`; the first 4B call hit the default 3-second timeout while the model loaded, then warmed calls answered in ~80–125 ms. If your model loads slowly, raise `timeout_s`. See the [full report](docs/reviews/local-scorer-benchmark-20261005T090911Z.md).

Use `/hae status` to check readiness — it reports key presence without exposing values (`credential: present/missing`) and names the effective endpoint. A missing key there means Hermes cannot see the variable: check the right `.env` file and restart.

### 2. Restart your Hermes session

Exit and launch your local Hermes agent again. If you use a gateway:

```bash
hermes gateway restart
```

Desktop connections can have separate agent processes; reconnect or restart the one behind your connection.

### 3. Choose a mode in chat

Start by checking the configured mode and scorer:

```text
/hae status
```

`/hae` is the plugin's slash command for status, probing and mode changes.

Use `/hae probe <text>` to score text you type without changing a request or storing a decision. For normal routing, choose one of the four modes:

```text
/hae auto
/hae once
/hae always
/hae off
```

`auto` evaluates each new message on exact routes verified for dynamic effort changes and otherwise keeps one decision per model and route. `once` always keeps one decision per model and route in the conversation. `always` evaluates every new user message. `off` sends no task text and changes no effort.

To keep that mode after a restart, save it in **Desktop → Capabilities → Plugins → Hermes Adaptive Effort → Mode**, or use the optional configuration below. Chat mode commands apply only to the current process.

You can check the scorer independently with `/hae probe What is 2 + 2?`. It scores the text you type, plus any optional configured classifier guidance, and stores no decision. Probe never attaches a target route or model profile. Scoring can incur provider charges.

### Prefer OpenRouter?

In the plugin's Desktop settings, select `openrouter` and enter your scorer model slug. Make `OPENROUTER_API_KEY` available to the serving Hermes process, then use the same chat commands above. No YAML editing is needed when your Desktop host exposes the settings form.

The scorer is separate from your conversation model. You choose which OpenRouter model to pay for; no model is silently selected and no failure falls back to Jev. The adapter requests JSON and caps completion output at 32 tokens; invalid answers or timeouts preserve the original request.

### Prefer OpenAI Decisions?

Select `openai_decision` and make `OPENAI_API_KEY` available to the serving Hermes process. The adapter sends one bounded task input and one ordered `low` / `medium` / `high` score question to the fixed [Decisions API](https://developers.openai.com/api/docs/guides/decisions) endpoint. It defaults to `gpt-6-luna`; `scorer_model` is optional and an explicit value is sent as configured. A refusal, API error, timeout or invalid answer leaves the request unchanged, with no retry or fallback to Jev. OpenAI documents ZDR for eligible customers; confirm your own project's eligibility in OpenAI's settings.

### Use your own hosted or local classifier?

Select `custom`, set `scorer_model`, and provide the complete endpoint URL in `custom_endpoint`. The endpoint is used as entered; the plugin does not append a path. Choose `systemone` (the default) or `chat_completions` in `custom_api_format`. A local example using llama-swap is `http://127.0.0.1:8099/v1/systemone`.

For a public endpoint that requires bearer authentication, set `custom_auth: bearer` and make `CUSTOM_SCORER_API_KEY` available to Hermes through its secret scope or the process environment. For a local endpoint without authentication, keep `custom_auth: none`; no key is looked up. The model name is required for either API format. There is no fallback to Jev or another provider if this endpoint fails.

The plugin validates the endpoint before making a request, rejects embedded credentials and URL fragments, masks query values in displayed status, and does not follow redirects. Choose a trusted endpoint: prompt text is sent there once you enable a routing mode, and this plugin cannot guarantee its retention policy.

### Prefer Cloudflare?

Select `cloudflare`, enter your 32-character hexadecimal account ID and choose `clef` or `clef-flash`. Make `CLOUDFLARE_AUTH_TOKEN` available to the serving Hermes process before launching or restarting it. Cloudflare has no fallback to another scorer.


<details>
<summary>Optional: persistent configuration, credentials and host compatibility</summary>

Settings live in your Hermes home's `config.yaml`. Merge the following into your existing `plugins` section, preserving your other entries:

```yaml
plugins:
  enabled:
    - hermes-adaptive-effort
  entries:
    hermes-adaptive-effort:
      settings:
        mode: auto
        scorer_provider: jev
        jev_model: jev-latest
```

For OpenRouter, change the settings to:

```yaml
settings:
  mode: auto
  scorer_provider: openrouter
  scorer_model: "YOUR_OPENROUTER_MODEL_SLUG"
```

For OpenAI Decisions, the model setting is optional:

```yaml
settings:
  mode: auto
  scorer_provider: openai_decision
  # scorer_model: gpt-6-luna # default; override only with a supported model
```

For Cloudflare:

```yaml
settings:
  mode: auto
  scorer_provider: cloudflare
  cloudflare_account_id: "YOUR_32_HEX_ACCOUNT_ID"
  cloudflare_model: clef
```

For a custom endpoint (local, unauthenticated System One):

```yaml
settings:
        mode: once
  scorer_provider: custom
  scorer_model: "effort-kev-08b"
  custom_endpoint: "http://127.0.0.1:8099/v1/systemone"
  custom_api_format: systemone
  custom_auth: none
```

For Chat Completions, set `custom_api_format: chat_completions`. For bearer authentication, set `custom_auth: bearer` and supply `CUSTOM_SCORER_API_KEY` through Hermes secret scope or the process environment.

Replace the model or account placeholder before using it. Use `/hae probe <text>` to inspect a sample without applying a request decision. Each `settings` example belongs under `plugins.entries.hermes-adaptive-effort`.

| Scorer | Credential | Model |
| --- | --- | --- |
| Jev (default) | `TYPESAFE_API_KEY` | `jev_model` (defaults to `jev-latest`) |
| OpenAI Decisions | `OPENAI_API_KEY` | `scorer_model` (defaults to `gpt-6-luna`) |
| OpenRouter | `OPENROUTER_API_KEY` | Explicit `scorer_model` slug |
| Cloudflare | `CLOUDFLARE_AUTH_TOKEN` | `clef` (default) or `clef-flash`; requires an account ID |
| Custom | None for `custom_auth: none`; `CUSTOM_SCORER_API_KEY` for `bearer` | Required `scorer_model`; exact `custom_endpoint`; System One or Chat Completions |

Credentials resolve through Hermes' secret scope, then the environment. A key set in a terminal reaches only processes that inherit that environment.

Hermes must support plugin `llm_request` middleware and session lifecycle hooks. Effort mapping also uses host internals. The optional Desktop interface needs the plugin SDK and focused-conversation state support. If your older Hermes CLI does not recognize `--enable`, run the install command without that flag, then `hermes plugins enable hermes-adaptive-effort`.

The repository root is the Hermes plugin payload. This is a Hermes plugin, not a pip package.

</details>

## Modes and commands

| Mode | Behavior |
| --- | --- |
| `off` (default) | No scoring or request changes; bounded route metadata may still appear in the Desktop popup |
| `auto` | Evaluate each new message on exact routes verified for dynamic effort changes; otherwise reuse one decision per model and route |
| `once` | Keep one decision per model and route in the conversation |
| `always` | Evaluate every new user message |

```text
/hae help
/hae status
/hae status json
/hae probe <text>
/hae auto|once|always|off
```

Mode commands are process-local and do not edit your config. Unknown commands or extra arguments return help without changing anything.

## From installation to every turn

Installing and enabling the plugin registers its request middleware and session cleanup hooks. **Installation does not classify anything.** The configured mode defaults to `off`, so the plugin leaves every request untouched and sends no task text to a scorer until you choose a routing mode. Selecting a scorer by itself also sends nothing. `/hae status` only reads status; `/hae probe <text>` is the explicit exception and scores the text you type, plus optional configured guidance.

When Hermes prepares a model request after a new user instruction, the plugin:

1. Checks the configured mode. With `off`, it records only bounded route/status metadata for the Desktop popup, then returns without scoring or changing the request. For other modes, the independent subagent gate is also checked.
2. Reads the latest user message from that request, not the opening message or the whole conversation. It first checks whether the request contains a supported writable effort field or can receive one on an exact supported route. Unsupported routes make no scorer call.
3. Chooses the decision scope. `always` evaluates each new user message. `once` keeps one decision per exact model and route. `auto` evaluates each message only for exact models with documented dynamic-effort support and a transport verified to keep effort changes out of the prompt cache; other routes keep one decision per model and route. All modes reuse one decision during a tool loop, including when the conversation model changes mid-turn. The `prompt_chars` setting caps the user text at 4,000 characters by default; custom guidance is separately capped at 2,000 characters. When `use_target_model_context` is enabled, a bounded route/profile prefix is added without reducing the task-text allowance.
4. Converts a valid score from `0` to `2` into `low` (< `0.5`), `medium` (< `1.5`) or `high`, then clamps that label to the effort values supported by the current route.
5. Changes an existing effort field, or injects one only on an exact verified route. It preserves explicitly disabled or malformed controls. If the chosen effort already matches, there is no rewrite or change notification.
6. Fails open on scorer errors, missing credentials, malformed responses or unsupported request shapes: the original request continues unchanged. Applied changes are shown by enabled status surfaces; task text, guidance and target context are not written to logs, status, or the change feed.

Enabling a routing mode authorizes sending task text and any configured classification guidance to the selected scorer. `use_target_model_context` defaults to false; enabling it also shares the target provider, exact model, API mode, observed effort and any matching local model profile during active request classification. Model profiles are exact-ID documentation summaries and do not prove that the current provider route supports an effort control. The observed value is reference context, not a recommendation. Probe remains text-only. Provider retention policies are separate: truncation does not guarantee non-retention. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints, but OpenRouter still processes the prompt. OpenAI documents ZDR support for eligible Decisions API customers; the plugin cannot verify account eligibility. This plugin cannot assure ZDR for Jev, Cloudflare or custom endpoints. Subagent goals are held transiently in memory and classified only when both the main mode and `subagent_mode` permit it.

### What guides the classifier

The built-in rubric considers task complexity, ambiguity, scope, number of reasoning steps, tool or research depth, and explicit speed/cost priorities. With `use_target_model_context`, the selected scorer also receives a local profile when the model ID exactly matches an entry in `model_profiles.json`. The catalog covers OpenAI, Anthropic, DeepSeek, Google Gemini, xAI Grok, Cohere, Mistral, Z.ai GLM, Moonshot Kimi, LongCat, Xiaomi MiMo, MiniMax, Meta Muse Spark Contributor, Alibaba Qwen, and Tencent Hy. It includes all 23 free model variants in OpenRouter's listing as reviewed on 2026-10-07, plus a separate `openrouter/free` router profile. OpenCode Go's current 30-model roster is represented by exact IDs; Space Bunny is listed under OpenCode Go because its upstream lab is undisclosed. This is a static snapshot, not a runtime model directory.

Add coverage with a data-only edit: exact model IDs, documented levels and defaults, a concise summary, an HTTPS source URL, and a review date. Keep profiles separate when controls differ. `effort_levels: []` means the documentation does not define discrete effort values; use the summary to distinguish a thinking toggle, a model without effort controls, and a non-generative task such as embeddings or reranking. It does not prove route support or unsupported status. Google's [Gemini thinking guide](https://ai.google.dev/gemini-api/docs/thinking) lists model-specific levels and defaults; xAI's [reasoning guide](https://docs.x.ai/developers/model-capabilities/text/reasoning) varies xhigh by Grok version, and its [multi-agent guide](https://docs.x.ai/developers/model-capabilities/text/multi-agent) uses effort to select agent count instead of reasoning depth. Cohere's [reasoning guide](https://docs.cohere.com/docs/reasoning) describes a thinking toggle and token budget, while its [compatibility guide](https://docs.cohere.com/docs/compatibility-api) maps only `none` and `high` to that toggle. Mistral's [reasoning guide](https://docs.mistral.ai/studio/conversations/reasoning) documents adjustable effort for its current Small, Medium, and Large models. Z.ai's [core parameter guide](https://docs.z.ai/guides/overview/concept-param) differentiates GLM-5.2/5.3 effort levels from older models' thinking controls; Moonshot's [thinking-model guide](https://platform.kimi.ai/docs/guide/use-thinking-models) documents K3 effort and K2 thinking behavior. OpenAI's [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning?api-mode=responses), Anthropic's [Effort guide](https://platform.claude.com/docs/en/build-with-claude/effort), and DeepSeek's [Responses API reference](https://api-docs.deepseek.com/api/create-response/) describe their provider-specific controls. Profiles are qualitative references, not task-specific recommendations or evidence about a proxy route. Operator guidance supplements, but cannot replace, the fixed `low` / `medium` / `high` score definitions.

## Settings

The Desktop settings form is a flat list. Its fields are ordered by task: mode, scorer and
provider-specific details, classification controls, advanced limits, then display switches.
Provider-prefixed labels make related fields easier to scan.

| Setting | Default | Purpose |
| --- | --- | --- |
| `mode` | `off` | Main routing mode; `off` disables scoring and rewrites |
| `subagent_mode` | `off` | Independent child-agent mode |
| `scorer_provider` | `jev` | `jev`, `openai_decision`, `openrouter`, `cloudflare` or `custom`; no automatic fallback |
| `jev_model` | `jev-latest` | Model sent to the Jev scoring endpoint |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint; ignored by other scorers |
| `scorer_model` | empty | Optional OpenAI Decisions model (defaults to `gpt-6-luna`); required OpenRouter slug or custom model name; ignored by Jev and Cloudflare |
| `cloudflare_account_id` | empty | Required 32-character hexadecimal account ID for Cloudflare |
| `cloudflare_model` | `clef` | Cloudflare model selector: `clef` or `clef-flash` |
| `custom_endpoint` | empty | Complete HTTP(S) endpoint URL for the custom scorer; no path is appended |
| `custom_api_format` | `systemone` | Custom request/response contract: `systemone` or `chat_completions` |
| `custom_auth` | `none` | Custom endpoint auth: `none` or `bearer` |
| `classification_instructions` | empty | Optional extra scoring guidance, capped at 2,000 characters; visible in Desktop plugin settings |
| `use_target_model_context` | `false` | Also send bounded target route metadata and an exact-ID local model profile to the scorer; does not apply to probe |
| `effort_models` | empty | Optional exact model IDs separated by commas or newlines; operator assertion that a known Responses/Chat Completions request shape accepts an effort field |
| `timeout_s` | `3.0` | HTTP timeout for classification |
| `prompt_chars` | `4000` | Maximum task characters sent to the selected scorer; not a retention control |
| `max_turns` | `64` | Bounded decision-cache capacity per process |
| `show_tui_status` | `true` | Show or hide the Hermes terminal Effort status item |
| `show_desktop_popup` | `true` | Show or hide the bottom-right Desktop effort chip, mode popup and its change notifications |

Set `classification_instructions` and `use_target_model_context` in **Desktop → Capabilities → Plugins → Hermes Adaptive Effort** or under `plugins.entries.hermes-adaptive-effort.settings` in `config.yaml`. For example, `classification_instructions: "Prefer low for short, clearly scoped requests; reserve high for ambiguous, multi-step work."` adds a preference to the shared rubric. Leave it empty to use the built-in guidance only. Target context is opt-in because it sends route metadata to the selected scorer. Both display switches can be changed in that same Desktop settings form or configuration section.

Jev sends the selected `jev_model` in its System One request body and accepts a full route, API base or bare host; `status` shows the selected model and effective URL. OpenAI Decisions posts to its fixed `/v1/decisions` endpoint with one score question and the configured model, or `gpt-6-luna` when `scorer_model` is empty; the native result must contain exactly one named `effort` score from 0 through 2. See the [Decisions guide](https://developers.openai.com/api/docs/guides/decisions) and [official changelog](https://developers.openai.com/api/docs/changelog). OpenRouter uses its fixed chat-completions endpoint and requests `provider.zdr=true` plus `data_collection=deny`; if no eligible route is available the request fails open rather than using a non-ZDR endpoint. Cloudflare uses the account-scoped Workers AI route for the selected `cloudflare_model` (`clef` or `clef-flash`); it requires `CLOUDFLARE_AUTH_TOKEN` and a valid account ID. The custom provider posts to the exact `custom_endpoint`, passes `scorer_model`, and supports System One (`state.prompt`, `questions`, and `answers.effort.score`) or Chat Completions (JSON response with a numeric score). `custom_auth: bearer` requires `CUSTOM_SCORER_API_KEY`; `none` does not inspect credentials. Status reports readiness without exposing keys and masks URL query values. Configured scorer failures leave requests unchanged and appear in status.

Cloudflare Clef and Clef Flash receive the same bounded `state.prompt` and typed score question as Jev. Their REST response must have `success: true` and a finite numeric `result.answers.effort.score` from 0 through 2. See the [Clef](https://developers.cloudflare.com/workers-ai/models/clef/), [Clef Flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/) and [Workers AI REST API](https://developers.cloudflare.com/workers-ai/get-started/rest-api/) documentation.

Cloudflare configuration failures use `account_missing` or `account_invalid`; transport and malformed response failures use the shared fail-open reason codes.

## A quiet interface

The CLI reports actual applied changes, such as `Effort changed: high -> low`. On hosts with the CLI status-item API, it also shows the last applied effort. Set `show_tui_status: false` to hide that status item; the setting is read on the next outgoing request.

The optional Desktop extension provides a compact **Routing mode** popup from the Effort chip. Hover over a mode for a short explanation, then use **More** to see the focused chat's effort and route, scorer/model readiness, guidance and gateway status. If effort is `N/A`, the details give the reason; a newly observed unsupported route or scorer failure also shows one warning toast while that chat is open. The toast says the request was sent unchanged and is not added to transcript history. **Activity** expands separately to show aggregate counters and the latest result across conversations; the sidebar already shows the last applied effort. Closing the popup returns to the compact view. After an effort rewrite reaches a request in the focused chat, Desktop updates that chat's native reasoning selector through a session-scoped Hermes API call and shows the same `from → to` transition in a toast. Background chats are cached for the chip when focused later, but their changes do not toast or synchronize the native selector. Hermes child-isolated turns cannot be synchronized through the parent session API; Desktop reports that limitation and leaves the selector unchanged. A rejected or unconfirmed selector update reports a short notice without affecting the request. This feature does not write global Hermes settings. Enable the extension in Desktop's plugin controls after installing the package; if the host does not discover the package's `desktop/` extension, that extension must be deployed to the host's `desktop-plugins/hermes-adaptive-effort/` directory separately. The agent plugin must also be enabled for its backend to work. Set `show_desktop_popup: false` in **Capabilities → Plugins → Hermes Adaptive Effort** to hide the chip, popup and notifications; focused-session synchronization continues independently of the display setting.

![Hermes Desktop Routing mode popup in its compact view](docs/images/routing-mode-popover.png)

The chip follows the focused conversation and backend/profile, including after a completed turn while its result remains cached. In `off`, the popup can still identify a route observed on that conversation; effort stays `N/A` until there is a usable decision. Applied-change toasts come from the session-scoped decision event, so the transition is paired with the effort shown for that focused chat. The anonymous `/changes` feed remains global and only refreshes status; it cannot supply the focused conversation's effort.

Middleware also publishes `plugin.hermes-adaptive-effort.decision.updated` with a versioned, allowlisted status snapshot so Desktop can receive child-process decisions promptly. The event is scoped by source connection, profile, runtime session, stream and revision. It contains no prompt, guidance text or credentials. The existing `/changes` endpoint remains an anonymous feed of applied effort values and does not identify a conversation.

## Compatibility and limits

A reasoning-capable model is not enough: its vendor must accept an effort control on the exact route. `unsupported` with zero probes usually means there is no field to change. Current `space-bunny` has a profile, while it and the legacy `space-bunny-free` alias remain outside the verified injection list. All active modes can add a missing field only for exact routes in the compatibility matrix, or for exact IDs an operator manually lists in `effort_models`. That list is an operator assertion, never vendor evidence; it uses no wildcards and works only with known Responses or Chat Completions containers. Generic OpenAI-compatible fallback does not prove route support.

The route vocabulary comes from Hermes plus narrow mappings for Kimi K3, GLM-5.2/5.3 and the exact Muse tiers listed below. Unknown routes use Hermes' broad OpenAI-compatible vocabulary for existing-field rewrites, which cannot guarantee vendor acceptance and never makes a route eligible for injection. **Known gap:** Ox Alpha / `x-preview-f-free` can reject `medium` with HTTP 400. That gap is documented rather than silently remapped.

Dynamic per-message decisions are limited to exact documented model/API pairs and transport paths for which effort changes do not alter the prompt prefix. API family alone, an existing effort field, or an `effort_models` entry is not proof of dynamic support. Other routes use one decision per exact model and route. These are routing rules, not measured cache-hit guarantees. Decisions can be evicted from the bounded stores or lost on reload/reset.

Cloudflare Clef is available through `scorer_provider: cloudflare`; its latency and scoring quality have not been evaluated live.

No cost saving, cache benefit or answer-quality improvement is claimed as measured. Scoring adds latency and can add cost. OpenRouter and Cloudflare adapters are covered by network-free tests; no live provider scorer evaluation has been run.

## Why this plugin exists

I wanted a small, unobtrusive plugin that does one job well: choose an appropriate reasoning effort without adding a large layer of features around the agent. I built it for my own daily use and intend to maintain it as I use it.

It started with Jev, but the job should not be tied to one scoring service. The neutral name and explicit scorer selection let the same routing rules work with Jev, a configured OpenRouter model, or Cloudflare Clef. The interface stays quiet, the controls remain explicit, and a scoring failure lets the conversation continue.

See [design choices](docs/DESIGN.md) for the trade-offs and their history.

## Updating, disabling and migrating

```bash
hermes plugins update hermes-adaptive-effort
hermes plugins disable hermes-adaptive-effort
```

Restart the serving agent after an update or enable/disable change. `/hae off` stops routing immediately for future requests in that process; set the persistent mode to `off` if it should stay off.

If you used the previous `jev-auto-effort` plugin ID, install the current payload, move settings from `plugins.entries.jev-auto-effort.settings` to `plugins.entries.hermes-adaptive-effort.settings`, disable the old ID and enable the current one. Replace the old Desktop extension too. Configure modes with `auto`, `once`, `always`, or `off`, and use `effort_models` for exact model IDs. Update source metadata to the renamed repository when using a managed install.

## More documentation

- [Design choices](docs/DESIGN.md): purpose, scope and trade-offs.
- [Runtime contracts](docs/CONTRACTS.md): route mappings, cache scope, failure states and public APIs.
- [Model compatibility](docs/MODEL_COMPATIBILITY.md): exact route evidence and OpenCode Go catalog injection/no-op outcomes.
- [Automated checks](docs/CI.md): Linux/Windows tests, required Hermes integration, security scans and their limits.
- [Development](docs/DEVELOPMENT.md): repository layout, network-free tests and lint commands.
- [Documentation index](docs/README.md): current references and dated review history.
- [Operator handoff](docs/HANDOFF.md): the recorded Iris/Windows rollout and unresolved operational items.

At revision `7ad378a`, the full network-free suite passed on Iris (Linux, Python 3.13.5): **385 tests**, including real Hermes plugin discovery and dispatcher integration. Tests use fake scorer transports and block network access. See the development guide to reproduce them.

### Exact-route effort-field support

The plugin defaults to `off`. A missing effort field is added only for positively verified routes or exact model IDs explicitly listed in `effort_models`. The current OpenCode Go registry includes exact Responses models, effort-only Chat Completions models, and paired controls only when the request already enables thinking. Every model published in the current Go docs has an explicit compatibility disposition in the [compatibility matrix](docs/MODEL_COMPATIBILITY.md); this does not mean every upstream route has been live-tested.

| Provider | Exact model IDs | `api_mode` | Wire values |
| --- | --- | --- | --- |
| OpenCode Zen (`opencode-zen`, `opencode`, `opencode_zen`, `zen`) | `muse-spark-1.3`, `muse-spark-1.2`, `muse-spark-1.3-contributor-free` | `codex_responses` | model-tier-specific Muse vocabulary; see matrix |
| OpenCode Go (`opencode-go`, `opencode_go`, `go`, `opencode-go-sub`) | exact catalog entries in the compatibility matrix | `codex_responses`, `chat_completions` | route-specific; includes effort-only and already-enabled paired controls |

Responses requests receive top-level `reasoning.effort`; Chat Completions requests receive top-level `reasoning_effort`. Paired routes require an already present `extra_body.thinking.type="enabled"`; the plugin never adds that toggle. `effort_models` accepts exact bare model IDs, comma or newline separated, with an optional provider prefix stripped for matching; it applies to any provider on `codex_responses` or `chat_completions`. It does not authorize Anthropic or unknown API modes, establish dynamic per-message support, or bypass disabled/malformed-control checks. It is an operator assertion, not evidence that the route accepts the field. When a route lacks verified dynamic support, the selected mode keeps a decision per exact model and route. Field-addition events use `from: absent`. Cache benefits remain unmeasured, and the plugin cannot catch a downstream provider rejection.
