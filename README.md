# Hermes Adaptive Effort

[![CI](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml) [![Security](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml)

Hermes Adaptive Effort asks an external scorer how much reasoning a task needs, then adjusts the effort setting already present in the outgoing model request. A greeting can use `low`; a more demanding task can use `high`.

Choose Jev (TypeSafe), a model you configure through OpenRouter, Cloudflare Clef / Clef Flash, or your own custom scorer endpoint. Jev remains the default and one option among several. The scorer is independent of the model answering the conversation: it only evaluates the task against the rubric and selects an effort level; Hermes still sends the request to your chosen conversation model.

The custom provider can connect to a hosted service or a local model server that implements either System One or OpenAI Chat Completions. Point it at the exact HTTP(S) endpoint and choose the model name your server exposes. This makes the classifier replaceable without changing the conversation model.

An initial Zenon trial loaded Kev 0.8B and 4B through llama.cpp and llama-swap on an RTX 4070 Ti. Across 180 warmed classifications per model on 60 synthetic English and French prompts, agreement with the fixed labels was 50% for 0.8B and 60% for 4B; neither model predicted `high`. The first 4B classification also hit the plugin's 3-second timeout, then all warmed calls succeeded. These exploratory results are not human-gold evaluation or a comparison with Jev. See the [full report](docs/reviews/local-scorer-benchmark-20261005T090911Z.md).

The plugin starts **off** and requires explicit permission to share prompt text with the selected scorer. If scoring fails, your original request continues unchanged. It changes only an existing effort field, leaves explicitly disabled reasoning alone, and keeps your conversation model.

**Tried live with Jev:** an October 3, 2026 evaluation exercised real Hermes conversations on Codex and OpenCode Go, including verification of outgoing HTTP bodies. OpenRouter and Cloudflare scoring have not been evaluated live. Cost savings, cache benefits and general answer-quality improvements remain unmeasured.

## Quick install

After the catalog entry is accepted, install by the plugin name without the repository subdirectory:

```bash
hermes plugins install hermes-adaptive-effort --enable
```

Until then, install directly from GitHub with the current subdirectory form:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort' --enable
```

Hermes handles installation; you do not need to clone this repo or install a Python package. Enabling the plugin makes it available, but its reasoning mode still starts **off**.

### 1. Make your scorer key available

Jev is the default. If `TYPESAFE_API_KEY` is already available to Hermes, skip this step. Otherwise, for a local session:

**Linux / macOS**

```bash
export TYPESAFE_API_KEY="YOUR_KEY"
```

**Windows PowerShell**

```powershell
$env:TYPESAFE_API_KEY = "YOUR_KEY"
```

Set the key before launching Hermes. These variables last for the terminal session. For a gateway service, use its environment or Hermes' secret scope. Keep keys out of `config.yaml`.

### 2. Restart your Hermes session

Exit and launch your local Hermes agent again. If you use a gateway:

```bash
hermes gateway restart
```

Desktop connections can have separate agent processes; reconnect or restart the one behind your connection.

### 3. Permit prompt sharing with your scorer

In **Desktop → Capabilities → Plugins → Hermes Adaptive Effort**, set **Prompt sharing consent** (`prompt_sharing_provider`) to `jev` for the default scorer. For another scorer, select that same provider in both **Scorer provider** and **Prompt sharing consent**.

This permits sending up to 4,000 characters of task text per classification by default. It does not control the provider's retention policy. Without matching consent, the plugin makes no scorer call and reports `prompt_consent_required`. If you use the CLI, add the consent setting through the persistent configuration below.

### 4. Choose a mode in chat

Start by inspecting decisions without changing effort:

```text
/hermes-adaptive-effort recommend
/hermes-adaptive-effort status
```

Send a normal message with reasoning enabled on a compatible route, then check `status` again. A `decided` entry shows the score and target. When you want decisions applied:

```text
/hermes-adaptive-effort auto
```

To keep that mode after a restart, save it in **Desktop → Capabilities → Plugins → Hermes Adaptive Effort → Mode**, or use the optional configuration below. Chat mode commands apply only to the current process.

You can check the scorer independently with `/hermes-adaptive-effort probe What is 2 + 2?`. It scores only that typed text and stores no decision. Scoring can incur provider charges.

### Prefer OpenRouter?

In the plugin's Desktop settings, select `openrouter`, enter your scorer model slug, and set **Prompt sharing consent** to `openrouter`. Make `OPENROUTER_API_KEY` available to the serving Hermes process, then use the same chat commands above. No YAML editing is needed when your Desktop host exposes the settings form.

The scorer is separate from your conversation model. You choose which OpenRouter model to pay for; no model is silently selected and no failure falls back to Jev. The adapter requests JSON and caps completion output at 32 tokens; invalid answers or timeouts preserve the original request.

### Use your own hosted or local classifier?

Select `custom`, set `scorer_model`, and provide the complete endpoint URL in `custom_endpoint`. The endpoint is used as entered; the plugin does not append a path. Choose `systemone` (the default) or `chat_completions` in `custom_api_format`. A local example using llama-swap is `http://127.0.0.1:8099/v1/systemone`.

For a public endpoint that requires bearer authentication, set `custom_auth: bearer` and make `CUSTOM_SCORER_API_KEY` available to Hermes through its secret scope or the process environment. For a local endpoint without authentication, keep `custom_auth: none`; no key is looked up. The model name is required for either API format. Set **Prompt sharing consent** to `custom` as well as choosing `custom` for **Scorer provider**. There is no fallback to Jev or another provider if this endpoint fails.

The plugin validates the endpoint before making a request, rejects embedded credentials and URL fragments, masks query values in displayed status, and does not follow redirects. Choose a trusted endpoint: prompt text is sent there after explicit consent, and this plugin cannot guarantee its retention policy.

### Prefer Cloudflare?

Select `cloudflare`, enter your 32-character hexadecimal account ID, choose `clef` or `clef-flash`, and set **Prompt sharing consent** to `cloudflare`. Make `CLOUDFLARE_AUTH_TOKEN` available to the serving Hermes process before launching or restarting it. Cloudflare has no fallback to another scorer.

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
        prompt_sharing_provider: jev
```

For OpenRouter, change the settings to:

```yaml
settings:
  mode: auto
  scorer_provider: openrouter
  scorer_model: "YOUR_OPENROUTER_MODEL_SLUG"
  prompt_sharing_provider: openrouter
```

For Cloudflare:

```yaml
settings:
  mode: auto
  scorer_provider: cloudflare
  cloudflare_account_id: "YOUR_32_HEX_ACCOUNT_ID"
  cloudflare_model: clef
  prompt_sharing_provider: cloudflare
```

For a custom endpoint (local, unauthenticated System One):

```yaml
settings:
  mode: recommend
  scorer_provider: custom
  scorer_model: "effort-kev-08b"
  custom_endpoint: "http://127.0.0.1:8099/v1/systemone"
  custom_api_format: systemone
  custom_auth: none
  prompt_sharing_provider: custom
```

For Chat Completions, set `custom_api_format: chat_completions`. For bearer authentication, set `custom_auth: bearer` and supply `CUSTOM_SCORER_API_KEY` through Hermes secret scope or the process environment.

Replace the model or account placeholder before using it. Start with `mode: recommend` instead of `auto` if you want to observe decisions first. Each `settings` example belongs under `plugins.entries.hermes-adaptive-effort`.

| Scorer | Credential | Model |
| --- | --- | --- |
| Jev (default) | `TYPESAFE_API_KEY` | Fixed `jev-latest` |
| OpenRouter | `OPENROUTER_API_KEY` | Explicit `scorer_model` slug |
| Cloudflare | `CLOUDFLARE_AUTH_TOKEN` | `clef` (default) or `clef-flash`; requires an account ID |
| Custom | None for `custom_auth: none`; `CUSTOM_SCORER_API_KEY` for `bearer` | Required `scorer_model`; exact `custom_endpoint`; System One or Chat Completions |

Credentials resolve through Hermes' secret scope, then the environment. A key set in a terminal reaches only processes that inherit that environment.

Hermes must support plugin `llm_request` middleware and session lifecycle hooks. Effort mapping also uses host internals. The optional Desktop interface needs the plugin SDK and focused-conversation state support. If your older Hermes CLI does not recognize `--enable`, run the install command without that flag, then `hermes plugins enable hermes-adaptive-effort`.

The `#hermes-adaptive-effort` URL fragment selects the payload directory inside this repository. This is a Hermes plugin, not a pip package.

</details>

## Modes and commands

| Mode | Behavior |
| --- | --- |
| `off` (default) | No scoring or rewriting |
| `recommend` | Score the task and report the target; keep the original effort |
| `auto` | Score each user turn and rewrite an existing effort field |
| `cache_safe` | Score per turn on recognized cache-neutral routes; otherwise reuse a session decision while cached |

```text
/hermes-adaptive-effort help
/hermes-adaptive-effort status
/hermes-adaptive-effort status json
/hermes-adaptive-effort probe <text>
/hermes-adaptive-effort off|recommend|auto|cache_safe
```

Mode commands are process-local and do not edit your config. Unknown commands or extra arguments return help without changing anything.

## How it works

1. Read the latest user text from the outgoing request, rather than the opening message or the whole conversation.
2. Check provider-specific prompt consent, then send a bounded excerpt to the selected scorer. A finite score from `0` to `2` becomes `low` below `0.5`, `medium` below `1.5`, or `high` otherwise.
3. Map that label to the route's available effort values.
4. In an applying mode, rewrite only an existing `extra_body.reasoning.effort`, `reasoning_effort` or top-level `reasoning.effort` slot. Explicitly disabled reasoning stays disabled.

A tool loop reuses its turn's decision instead of calling the scorer for every model request. A new user turn gets a new classification in `auto`. A new classification does not necessarily change the effort: the existing value may match, or a route may offer only a narrow set of levels.

Missing consent, credentials or required settings, timeouts, transport errors and malformed scores leave the request unchanged. A request without a writable effort field is `unsupported` and makes zero scorer calls. A failed or unsupported decision is not retried within the same turn.

### Prompt privacy

The plugin itself does not persist prompts or include them in logs or status. The `prompt_chars` cap limits the text sent; truncation does not guarantee provider non-retention. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints, while still sending the text to OpenRouter for processing. This plugin cannot assure ZDR for Jev, Cloudflare or custom endpoints.

Subagents use the parent-written goal as their task text. Goals are held transiently in memory and classified only when the independent `subagent_mode` setting permits it and the main mode is enabled. Child scoring follows the same provider consent gate.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `mode` | `off` | Main routing mode |
| `subagent_mode` | `off` | Independent child-agent mode |
| `scorer_provider` | `jev` | `jev`, `openrouter`, `cloudflare` or `custom`; no automatic fallback |
| `scorer_model` | empty | Required OpenRouter or custom model name; ignored by Jev and Cloudflare |
| `custom_endpoint` | empty | Complete HTTP(S) endpoint URL for the custom scorer; no path is appended |
| `custom_api_format` | `systemone` | Custom request/response contract: `systemone` or `chat_completions` |
| `custom_auth` | `none` | Custom endpoint auth: `none` or `bearer` |
| `cloudflare_account_id` | empty | Required 32-character hexadecimal account ID for Cloudflare |
| `cloudflare_model` | `clef` | Cloudflare model selector: `clef` or `clef-flash` |
| `prompt_sharing_provider` | `none` | Explicitly permit sending prompt text to this scorer (`none`, `jev`, `openrouter`, `cloudflare` or `custom`); must match `scorer_provider` |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint; ignored by other scorers |
| `timeout_s` | `3.0` | HTTP timeout for classification |
| `max_turns` | `64` | Bounded decision-cache capacity per process |
| `prompt_chars` | `4000` | Maximum task characters sent after matching provider consent; not a retention control |

Jev accepts a full route, API base or bare host; `status` shows the effective URL. OpenRouter uses its fixed chat-completions endpoint and requests `provider.zdr=true` plus `data_collection=deny`; if no eligible route is available the request fails open rather than using a non-ZDR endpoint. Cloudflare uses the account-scoped Workers AI route for the selected `cloudflare_model` (`clef` or `clef-flash`); it requires `CLOUDFLARE_AUTH_TOKEN` and a valid account ID. The custom provider posts to the exact `custom_endpoint`, passes `scorer_model`, and supports System One (`state.prompt`, `questions`, and `answers.effort.score`) or Chat Completions (JSON response with a numeric score). `custom_auth: bearer` requires `CUSTOM_SCORER_API_KEY`; `none` does not inspect credentials. Status reports readiness without exposing keys and masks URL query values. Configured scorer failures leave requests unchanged and appear in status.

Cloudflare Clef and Clef Flash receive the same bounded `state.prompt` and typed score question as Jev. Their REST response must have `success: true` and a finite numeric `result.answers.effort.score` from 0 through 2. See the [Clef](https://developers.cloudflare.com/workers-ai/models/clef/), [Clef Flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/) and [Workers AI REST API](https://developers.cloudflare.com/workers-ai/get-started/rest-api/) documentation.

Cloudflare configuration failures use `account_missing` or `account_invalid`; transport and malformed response failures use the shared fail-open reason codes.

## A quiet interface

The CLI reports actual applied changes, such as `Effort changed: high -> low`. On hosts with the CLI status-item API, it also shows the last applied effort.

The optional Desktop extension provides a small effort chip, a details pane and mode controls. Enable it in Desktop's plugin controls after installing the package; if the host does not discover the package's `desktop/` extension, that extension must be deployed to the host's `desktop-plugins/hermes-adaptive-effort/` directory separately. The agent plugin must also be enabled for its backend to work.

The chip follows the focused conversation and backend/profile, including after a completed turn while its result remains cached. It shows `N/A` when there is no usable decision for that chat. Change notifications and the pane's latest transition reflect activity across conversations.

## What has been evaluated

On **October 3, 2026**, the deployed Jev plugin was exercised through Hermes's real middleware with Codex (`gpt-6.1-sol`) and OpenCode Go (`deepseek-v4.1-flash`) conversation requests. Fourteen synthetic cases completed, comprising fifteen primary conversation API calls and ten successful live Jev classifications. The cases covered changing task complexity, off/recommend modes, scorer failure, unsupported and disabled reasoning, and a real tool loop.

HTTP-body checks confirmed `reasoning.effort: low` reached the Codex responses route and `reasoning_effort: medium` reached the OpenCode Go chat-completions route. The recorded ten successful Jev classifications took 241–368 ms (mean 287 ms). These are observations from one small run, not performance or quality benchmarks.

The run also found a Desktop status issue in the evaluated revision: Hermes's completed-turn hook cleared the decision ledger, so the focused-conversation chip lost its result after delivery even though the effort change reached the request. Current source retains bounded decisions on completed-turn events and clears them at conversation finalize/reset boundaries. The October 3 report does not verify that fix in a subsequent live run.

The evaluated payload still used the former `jev-auto-effort` identity; it predates the current provider-consent setup.

OpenRouter and Cloudflare adapters have network-free test coverage, but no live scorer evaluation has been run for either. The October 3 live report predates the later prompt-consent and Clef Flash selector changes and does not validate them. No cost saving, cache benefit or general answer-quality improvement is claimed as measured. Scoring adds latency and can add cost.

## Compatibility and residual risk

A reasoning-capable model is not enough: its Hermes provider route must expose a writable effort field. `unsupported` with zero probes usually means there is no field to change. This has been observed with OpenCode Go's `space-bunny-free` profile. The plugin deliberately does not add one.

The route vocabulary comes from Hermes plus narrow mappings for Kimi K3 and GLM-5.2/5.3. Unknown routes use Hermes' broad OpenAI-compatible vocabulary, which cannot guarantee vendor acceptance. **Known gap:** Ox Alpha / `x-preview-f-free` can reject `medium` with HTTP 400. That gap is documented rather than silently remapped.

`cache_safe` treats `chat_completions` and `codex_responses` as cache-neutral, and Anthropic/unknown API modes as cache-hostile. These are routing rules, not measured cache-hit guarantees. Session decisions can be evicted from the bounded cache or lost on reload/reset.

## Why this plugin exists

I wanted a small, unobtrusive plugin that does one job well: choose an appropriate reasoning effort without adding a large layer of features around the agent. I built it for my own daily use and intend to maintain it as I use it.

It started with Jev, but the job should not be tied to one scoring service. The neutral name and explicit scorer selection let the same routing rules work with Jev, a configured OpenRouter model, or Cloudflare Clef. The interface stays quiet, the controls remain explicit, and a scoring failure lets the conversation continue.

See [design choices](docs/DESIGN.md) for the trade-offs and their history.

## Updating, disabling and migrating

```bash
hermes plugins update hermes-adaptive-effort
hermes plugins disable hermes-adaptive-effort
```

Restart the serving agent after an update or enable/disable change. `/hermes-adaptive-effort off` stops routing immediately for future requests in that process; set the persistent mode to `off` if it should stay off.

If you used `jev-auto-effort`, install the new payload, move your old settings from `plugins.entries.jev-auto-effort.settings` to `plugins.entries.hermes-adaptive-effort.settings`, disable the old ID and enable the new one. Replace the old Desktop extension too. Keep your existing mode deliberately, and run only one middleware copy to avoid duplicate scoring. Update source metadata to the renamed repository when using a managed install.

## More documentation

- [Design choices](docs/DESIGN.md): purpose, scope and trade-offs.
- [Runtime contracts](docs/CONTRACTS.md): route mappings, cache scope, failure states and public APIs.
- [Automated checks](docs/CI.md): Linux/Windows tests, required Hermes integration, security scans and their limits.
- [Development](docs/DEVELOPMENT.md): repository layout, network-free tests and lint commands.
- [Documentation index](docs/README.md): current references and dated review history.
- [Operator handoff](docs/HANDOFF.md): the recorded Iris/Windows rollout and unresolved operational items.

The recorded October 3 baseline passed 217 Windows tests, including real Hermes plugin discovery/dispatcher integration. Tests use fake scorer transports and block network access; that baseline is not verification of every later source change. See the development guide to reproduce the checks.
