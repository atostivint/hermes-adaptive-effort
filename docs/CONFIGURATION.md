# Configuration

[Quick start](../README.md#quick-start) · [Usage and troubleshooting](USAGE.md) · [Runtime contracts](CONTRACTS.md)

The scorer classifies the task; your conversation model answers it. Selecting a scorer while `mode: off` sends no task text. Enabling `auto`, `once` or `always` authorizes sharing the bounded task, configured guidance and—on exact named-choice routes—the allowed effort names with that scorer.

## Save settings

In Desktop, open **Capabilities → Plugins → Hermes Adaptive Effort**. The settings form groups fields by their labels: mode, scorer, classification, advanced limits and display.

You can also merge settings into your Hermes home's `config.yaml`:

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

Preserve other enabled plugins and existing entries. Each `settings:` example below replaces only the settings block for this plugin. Supply secrets separately.

Chat commands such as `/hae auto` override the mode for future requests in the current process only. Save `plugins.entries.hermes-adaptive-effort.settings.mode` to keep it after restart. A running process can retain its chat override until it reloads.

## Make credentials available

Required keys resolve through Hermes' secret scope first, then the process environment. `/hae status` reports presence without exposing values.

| Scorer | Credential | Other required configuration |
| --- | --- | --- |
| Jev | `TYPESAFE_API_KEY` | None; `jev_model` defaults to `jev-latest` |
| OpenAI Decisions | `OPENAI_API_KEY` | None; empty `scorer_model` uses `gpt-6-luna` |
| OpenRouter | `OPENROUTER_API_KEY` | Explicit `scorer_model` slug |
| Cloudflare | `CLOUDFLARE_AUTH_TOKEN` | 32-character hexadecimal `cloudflare_account_id` |
| Custom, `custom_auth: none` | No key lookup | Exact endpoint and explicit model |
| Custom, `custom_auth: bearer` | `CUSTOM_SCORER_API_KEY` | Exact endpoint and explicit model |

### Persistent keys

Add the appropriate variable to the `.env` file used by the serving Hermes profile:

```dotenv
TYPESAFE_API_KEY=YOUR_KEY
```

For the default profile, the verified Hermes source uses `~/.hermes/.env` on Linux/macOS and `%LOCALAPPDATA%\hermes\.env` on Windows. Respect the active profile and any `HERMES_HOME` override or configured data-directory suffix; a key in another profile's file may not reach the serving process.

Restart the serving process after changing its environment. Keep the file private and out of Git. Never place keys in `config.yaml`.

### Keys for one terminal session

Set the variable before launching Hermes from the same terminal.

Linux / macOS:

```bash
export TYPESAFE_API_KEY="YOUR_KEY"
```

Windows PowerShell:

```powershell
$env:TYPESAFE_API_KEY = "YOUR_KEY"
```

Substitute the selected scorer's variable name. An already running gateway or Desktop agent does not inherit a later shell export: configure that service's environment and restart it.

A secrets manager can supply the environment before launch. The plugin reads Hermes' secret scope and environment; it has no direct Bitwarden, 1Password or vault integration.

## Configure your scorer

### Jev

Jev is the default. Obtain `TYPESAFE_API_KEY` from your TypeSafe account.

```yaml
settings:
  mode: auto
  scorer_provider: jev
  jev_model: jev-latest
```

`endpoint` defaults to `https://api.typesafe.ai/v1/systemone`. Jev accepts a complete scoring route, API base or bare host; status shows the effective URL. Changing `jev_model` passes that name to the endpoint.

### OpenAI Decisions

Make `OPENAI_API_KEY` from your OpenAI API account available to Hermes.

```yaml
settings:
  mode: auto
  scorer_provider: openai_decision
```

The plugin uses the fixed `https://api.openai.com/v1/decisions` endpoint and defaults to `gpt-6-luna`. `scorer_model` can pass an explicit model, but the [official Decisions guide](https://developers.openai.com/api/docs/guides/decisions) listed only `gpt-6-luna` during the 2026-10-07 review. The API was in public beta at that review.

For an exact model/API route with a registered effort vocabulary, the adapter asks one choice question containing those named levels and accepts exactly one of them. Other routes use the legacy ordered score question. A refusal, unavailable model or invalid response leaves the original request unchanged.

### OpenRouter

Create an [OpenRouter key](https://openrouter.ai/keys) and set an exact model slug you intend to use.

```yaml
settings:
  mode: auto
  scorer_provider: openrouter
  scorer_model: "YOUR_OPENROUTER_MODEL_SLUG"
```

The model is required; the plugin does not choose one for you. It uses OpenRouter's fixed Chat Completions endpoint, requests JSON and caps the completion at 32 tokens. The request sets `provider.zdr: true` and `data_collection: deny`. If no eligible endpoint is available, classification fails open.

### Cloudflare Clef or Clef Flash

From **Cloudflare dashboard → Workers AI → Use REST API**, create a Workers AI API token and copy the Account ID. Cloudflare's [REST setup guide](https://developers.cloudflare.com/workers-ai/get-started/rest-api/) describes the token template and permissions.

```yaml
settings:
  mode: auto
  scorer_provider: cloudflare
  cloudflare_account_id: "YOUR_32_HEX_ACCOUNT_ID"
  cloudflare_model: clef
```

Provide the token as `CLOUDFLARE_AUTH_TOKEN`. Replace the account placeholder with 32 hexadecimal characters. Select `clef` or `clef-flash`; the plugin builds the corresponding account-scoped Workers AI endpoint.

### Custom hosted or local endpoint

Supply the full endpoint URL, served model name and matching API format:

```yaml
settings:
  mode: once
  scorer_provider: custom
  scorer_model: "YOUR_SERVED_MODEL_NAME"
  custom_endpoint: "http://127.0.0.1:8099/v1/systemone"
  custom_api_format: systemone
  custom_auth: none
```

This example assumes you already run a local System One server at that address. A local server such as llama.cpp or a llama-swap setup must expose the exact route and model you configure.

For an OpenAI-compatible Chat Completions server, use its complete URL, such as `http://127.0.0.1:8099/v1/chat/completions`, and set `custom_api_format: chat_completions`. On exact registered routes, the server must return only `{"effort":"<allowed level>"}`; on other routes and for `/hae probe`, it must return a finite numeric `score` in `0..2`. System One uses `answers.effort.choice` for named choices or `answers.effort.score` for the legacy rubric. The server must implement the matching named-choice response when you use a registered route; an old score-only server fails open, with no second call or score fallback. See [adapter contracts](CONTRACTS.md#scorer-adapter-contracts).

For bearer authentication, set `custom_auth: bearer` and supply `CUSTOM_SCORER_API_KEY`. With `none`, the plugin does not look up a key. It uses the endpoint exactly as configured, rejects embedded credentials and fragments, and does not follow redirects. Status masks query parameter values.

Local scoring needs no cloud account or token. Task text goes to the selected local endpoint; review any logging, proxying or remote forwarding by that server. A cold model can exceed the default timeout. The [local scorer trial](reviews/local-scorer-benchmark-20261005T090911Z.md) records startup timeouts and limited synthetic-label agreement; it is not a quality recommendation.

## All settings

The defaults below match `middleware.DEFAULTS` and the manifest settings schema.

| Setting | Default | Purpose |
| --- | --- | --- |
| `mode` | `off` | Main mode: `auto`, `once`, `always`, `off` |
| `subagent_mode` | `off` | Independent child mode; the main mode must also be active |
| `scorer_provider` | `jev` | `jev`, `openai_decision`, `openrouter`, `cloudflare`, `custom` |
| `jev_model` | `jev-latest` | Jev model name |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint; ignored by other scorers |
| `scorer_model` | empty | Optional for OpenAI Decisions; required for OpenRouter/custom |
| `cloudflare_account_id` | empty | Cloudflare account ID |
| `cloudflare_model` | `clef` | `clef` or `clef-flash` |
| `custom_endpoint` | empty | Exact HTTP(S) scoring URL |
| `custom_api_format` | `systemone` | `systemone` or `chat_completions`; exact-choice routes require named-choice support |
| `custom_auth` | `none` | `none` or `bearer` |
| `classification_instructions` | empty | Extra guidance, capped at 2,000 characters |
| `use_target_model_context` | `false` | Include bounded target route/profile context when classifying a request |
| `effort_models` | empty | Exact model IDs whose effort support the operator asserts |
| `timeout_s` | `3.0` | Timeout in seconds for one scoring request |
| `prompt_chars` | `4000` | Maximum task characters sent; not a retention control |
| `max_turns` | `64` | Capacity of each bounded turn/route decision ledger |
| `show_tui_status` | `true` | Show terminal Effort status item |
| `show_desktop_popup` | `true` | Show Desktop chip, popup and notifications |

### Guide classification

```yaml
settings:
  classification_instructions: "Prefer low for short, clearly scoped requests; reserve high for ambiguous, multi-step work."
  use_target_model_context: false
```

Merge these fields into your scorer settings. Guidance supplements the fixed legacy score rubric and named-choice instructions; it cannot change the route's allowed values or output format. The evaluator considers complexity, ambiguity, scope, reasoning steps, tool/research depth and explicit speed/cost priorities. A longer prompt or technical subject alone is not a reason to select more effort.

With `use_target_model_context: true`, a request classification also shares target provider, exact model, API mode, observed effort and any matching local model profile. The context has its own 1,400-character cap. The observed effort is reference context, not a desired score. Profiles use exact IDs and never grant route support. Probe remains typed text plus guidance, without target context.

### Assert support for an exact model

`effort_models` accepts comma- or newline-separated exact model IDs, with an optional provider/namespace prefix stripped for matching. It permits adding a missing field only to known Responses or Chat Completions containers.

This is an operator assertion. It cannot establish dynamic effort support, enable thinking, bypass disabled/malformed controls, or authorize an Anthropic/unknown API shape. Check the [compatibility matrix](MODEL_COMPATIBILITY.md#operator-force-list-for-unverified-model-ids) before using it.

## Data sharing

| Action | Data sent to the selected scorer |
| --- | --- |
| Install, select a scorer, read status, or leave routing `off` | None |
| Eligible classification in an active mode | Bounded latest user text, optional guidance, and the route's allowed effort names when using named-choice classification |
| Child classification with both gates enabled | Bounded parent-written goal, optional guidance, and allowed effort names on named-choice routes |
| Request classification with target context enabled | The above task/guidance plus bounded target route/profile context |
| Explicit `probe`, including while `off` | Operator-typed text plus optional guidance; no target context |

A reused decision makes no new scoring request. The plugin sends neither the whole conversation nor tool results as task text. The allowed-choice list is part of the question; model identity, observed effort and any local model profile remain governed by `use_target_model_context`. It excludes prompts, guidance contents and composed target context from logs, status and change feeds. Child goals exist transiently in bounded memory.

These limits describe the plugin. They do not guarantee how an external service retains data:

- OpenRouter requests require ZDR endpoints and deny data-collecting endpoints. OpenRouter still processes the input; see its [ZDR policy and routing controls](https://openrouter.ai/docs/guides/features/zdr).
- OpenAI documents Decisions ZDR support for eligible customers. The plugin cannot verify account eligibility; see the [Decisions guide](https://developers.openai.com/api/docs/guides/decisions).
- The plugin gives no ZDR guarantee for Jev, Cloudflare or custom endpoints.

No provider falls back to another scorer. Missing configuration, transport errors, timeouts or malformed scores leave requests unchanged and appear in status. See [troubleshooting](USAGE.md#troubleshooting).
