# Hermes Adaptive Effort

[![CI](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml) [![Security](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml)

A small Hermes plugin that chooses reasoning effort for each user turn, using an external scorer. It changes an existing effort setting and can fill a missing field on exact, documented model routes.

Built for everyday use: one focused job, a quiet interface, and a scorer you can choose. Choose Jev (TypeSafe), a model you configure through OpenRouter, Cloudflare Clef / Clef Flash, or your own custom scorer endpoint. Jev is the default. The scorer is independent of the model answering the conversation: it only evaluates the task against the rubric and selects an effort level; Hermes still sends the request to your chosen conversation model.

The custom provider can connect to a hosted service or a local model server that implements either System One or OpenAI Chat Completions. Point it at the exact HTTP(S) endpoint and choose the model name your server exposes. This makes the classifier replaceable without changing the conversation model.

An initial Zenon trial loaded Kev 0.8B and 4B through llama.cpp and llama-swap on an RTX 4070 Ti. Across 180 warmed classifications per model on 60 synthetic English and French prompts, agreement with the fixed labels was 50% for 0.8B and 60% for 4B; neither model predicted `high`. The first 4B classification also hit the plugin's 3-second timeout, then all warmed calls succeeded. These exploratory results are not human-gold evaluation or a comparison with Jev. See the [full report](docs/reviews/local-scorer-benchmark-20261005T090911Z.md).

The plugin is opt-in and starts **off**. If scoring fails, your original request continues unchanged. It does not switch your conversation model or add tools; `auto` and `inject` add a reasoning setting only on exact supported routes or exact operator-listed model IDs.

## Quick install

Install directly from the GitHub repository root:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort' --enable
```

The plugin manifest and payload now live at the repository root, so no subdirectory fragment is needed. The previous `#hermes-adaptive-effort` suffix selected the old nested payload directory. A root install retains Git metadata, which lets `hermes plugins update hermes-adaptive-effort` update the plugin normally.

Hermes handles installation; you do not need to clone this repo or install a Python package. Enabling the plugin makes it available, but its reasoning mode still starts **off**.

### 1. Make your scorer key available

Scorer keys are **not** pre-installed: you must provide the key for whichever scorer you select. Keys resolve through Hermes' secret scope (your profile's `.env` file) first, then the process environment. Without the key, every classification fails open with `credential_missing` and your request continues unchanged. Never put keys in `config.yaml`.

#### Where to get each key

| Scorer | Key | Where to get it |
| --- | --- | --- |
| Jev (default) | `TYPESAFE_API_KEY` | From your TypeSafe account — the same key used by the Jev approvals plugin against `https://api.typesafe.ai`. No model to choose: the scorer is fixed to `jev-latest`. |
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

One line per scorer if you run several (`OPENROUTER_API_KEY=…`, `CLOUDFLARE_AUTH_TOKEN=…`, `CUSTOM_SCORER_API_KEY=…`). For a gateway service, use its environment or service `EnvironmentFile` instead — a key exported in your terminal does not reach an already-running service.

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
export OPENROUTER_API_KEY="$(op read 'op://Private/hermes/OPENROUTER_API_KEY')"
```

Launch Hermes from that same shell afterwards. If you sync `.env` from a manager, keep the file readable only by you and never commit it.

#### No key? Use a local model (no account, no token)

Select the `custom` scorer and point it at a local OpenAI-compatible server. No key is looked up when `custom_auth: none`:

1. Start a local server that serves either System One or Chat Completions — e.g. `llama-server` directly, or `llama-swap` when you switch between models. Note the exact URL and the model name the server exposes.
2. In the plugin's Desktop settings (or `config.yaml`), set `scorer_provider: custom`, `custom_endpoint` to the **exact** URL (nothing is appended; a tested llama-swap example is `http://127.0.0.1:8099/v1/systemone`), `scorer_model` to the served name (e.g. `effort-kev-08b`), and `custom_api_format` to `systemone` or `chat_completions` to match your server. Keep `custom_auth: none`.
3. Choose a routing mode (`recommend` to observe, `auto` to apply) — enabling a mode authorizes sending task text to the selected scorer; while the mode stays `off` nothing is sent. Prompts stay on your machine with a local endpoint, but the plugin still only sends text once routing is enabled.

Local-model caveats, measured on one Windows/RTX 4070 Ti box (not a recommendation): Kev 0.8B agreed with the synthetic fixed labels 50% of the time, Kev 4B 60%, and neither predicted `high`; the first 4B call hit the default 3-second timeout while the model loaded, then warmed calls answered in ~80–125 ms. If your model loads slowly, raise `timeout_s`. See the [full report](docs/reviews/local-scorer-benchmark-20261005T090911Z.md).

Use `/hae status` to check readiness — it reports key presence without exposing values (`credential: present/missing`) and names the effective endpoint. The original `/hermes-adaptive-effort` command remains available as an alias. A missing key there means Hermes cannot see the variable: check the right `.env` file and restart.

### 2. Restart your Hermes session

Exit and launch your local Hermes agent again. If you use a gateway:

```bash
hermes gateway restart
```

Desktop connections can have separate agent processes; reconnect or restart the one behind your connection.

### 3. Choose a mode in chat

Start by inspecting decisions without changing effort:

```text
/hae recommend
/hae status
```

The short command is `/hae` (“Hermes Adaptive Effort”); existing `/hermes-adaptive-effort` commands continue to work.

Send a normal message with reasoning enabled on a compatible route, then check `status` again. A `decided` entry shows the score and target. When you want decisions applied:

```text
/hae auto
```

To keep that mode after a restart, save it in **Desktop → Capabilities → Plugins → Hermes Adaptive Effort → Mode**, or use the optional configuration below. Chat mode commands apply only to the current process.

You can check the scorer independently with `/hae probe What is 2 + 2?`. It scores only that typed text and stores no decision. Scoring can incur provider charges.

### Prefer OpenRouter?

In the plugin's Desktop settings, select `openrouter` and enter your scorer model slug. Make `OPENROUTER_API_KEY` available to the serving Hermes process, then use the same chat commands above. No YAML editing is needed when your Desktop host exposes the settings form.

The scorer is separate from your conversation model. You choose which OpenRouter model to pay for; no model is silently selected and no failure falls back to Jev. The adapter requests JSON and caps completion output at 32 tokens; invalid answers or timeouts preserve the original request.

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
```

For OpenRouter, change the settings to:

```yaml
settings:
  mode: auto
  scorer_provider: openrouter
  scorer_model: "YOUR_OPENROUTER_MODEL_SLUG"
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
  mode: recommend
  scorer_provider: custom
  scorer_model: "effort-kev-08b"
  custom_endpoint: "http://127.0.0.1:8099/v1/systemone"
  custom_api_format: systemone
  custom_auth: none
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

The repository root is the Hermes plugin payload. This is a Hermes plugin, not a pip package.

</details>

## Modes and commands

| Mode | Behavior |
| --- | --- |
| `off` (default) | No scoring or rewriting |
| `recommend` | Score the task and report the target; keep the original effort |
| `auto` | Score each user turn, rewrite an existing effort field, or inject on an exact verified model route when it is absent |
| `cache_safe` | Score per turn on recognized cache-neutral routes; otherwise reuse a session decision while cached |
| `inject` | Compatibility mode: cache-safe policy plus exact-route injection |

```text
/hae help
/hae status
/hae status json
/hae probe <text>
/hae off|recommend|auto|cache_safe|inject
```

`/hermes-adaptive-effort` remains an equivalent long-form alias.

Mode commands are process-local and do not edit your config. Unknown commands or extra arguments return help without changing anything.

## How it works

1. Read the latest user text from the outgoing request, rather than the opening message or the whole conversation.
2. Send a bounded excerpt to the selected scorer. A finite score from `0` to `2` becomes `low` below `0.5`, `medium` below `1.5`, or `high` otherwise.
3. Map that label to the route's available effort values.
4. Rewrite an existing effort slot, or add one only when the exact provider, model, API mode and carrier are listed in the [model compatibility matrix](docs/MODEL_COMPATIBILITY.md). Explicitly disabled reasoning stays disabled.

A tool loop reuses its turn's decision instead of calling the scorer for every model request. `auto` classifies new user turns separately, except an injected decision on a cache-unsafe route stays pinned while its session entry remains cached. A new classification does not necessarily change the effort: the existing value may match, or a route may offer only a narrow set of levels.

Enabling a routing mode authorizes sending task text to the selected scorer; choosing a provider while leaving mode `off` sends nothing. A typed `probe` explicitly sends only its argument. The `prompt_chars` setting caps the text sent (4,000 characters by default); truncation does not guarantee provider non-retention. The plugin itself does not persist prompts or include them in logs or status. Provider retention policies are separate. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; this does not keep the prompt from being processed by OpenRouter. This plugin cannot assure ZDR for Jev, Cloudflare or custom endpoints. Subagent goals are held transiently in memory and classified only when the independent `subagent_mode` setting permits it and the main mode is enabled.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `mode` | `off` | Main routing mode |
| `force_injection_models` | empty | Optional exact model IDs separated by commas or newlines; asserts support for known Responses/Chat Completions shapes in `auto`/`inject` only |
| `subagent_mode` | `off` | Independent child-agent mode |
| `scorer_provider` | `jev` | `jev`, `openrouter`, `cloudflare` or `custom`; no automatic fallback |
| `scorer_model` | empty | Required OpenRouter or custom model name; ignored by Jev and Cloudflare |
| `custom_endpoint` | empty | Complete HTTP(S) endpoint URL for the custom scorer; no path is appended |
| `custom_api_format` | `systemone` | Custom request/response contract: `systemone` or `chat_completions` |
| `custom_auth` | `none` | Custom endpoint auth: `none` or `bearer` |
| `cloudflare_account_id` | empty | Required 32-character hexadecimal account ID for Cloudflare |
| `cloudflare_model` | `clef` | Cloudflare model selector: `clef` or `clef-flash` |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint; ignored by other scorers |
| `timeout_s` | `3.0` | HTTP timeout for classification |
| `max_turns` | `64` | Bounded decision-cache capacity per process |
| `prompt_chars` | `4000` | Maximum task characters sent to the selected scorer; not a retention control |

Jev accepts a full route, API base or bare host; `status` shows the effective URL. OpenRouter uses its fixed chat-completions endpoint and requests `provider.zdr=true` plus `data_collection=deny`; if no eligible route is available the request fails open rather than using a non-ZDR endpoint. Cloudflare uses the account-scoped Workers AI route for the selected `cloudflare_model` (`clef` or `clef-flash`); it requires `CLOUDFLARE_AUTH_TOKEN` and a valid account ID. The custom provider posts to the exact `custom_endpoint`, passes `scorer_model`, and supports System One (`state.prompt`, `questions`, and `answers.effort.score`) or Chat Completions (JSON response with a numeric score). `custom_auth: bearer` requires `CUSTOM_SCORER_API_KEY`; `none` does not inspect credentials. Status reports readiness without exposing keys and masks URL query values. Configured scorer failures leave requests unchanged and appear in status.

Cloudflare Clef and Clef Flash receive the same bounded `state.prompt` and typed score question as Jev. Their REST response must have `success: true` and a finite numeric `result.answers.effort.score` from 0 through 2. See the [Clef](https://developers.cloudflare.com/workers-ai/models/clef/), [Clef Flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/) and [Workers AI REST API](https://developers.cloudflare.com/workers-ai/get-started/rest-api/) documentation.

Cloudflare configuration failures use `account_missing` or `account_invalid`; transport and malformed response failures use the shared fail-open reason codes.

## A quiet interface

The CLI reports actual applied changes, such as `Effort changed: high -> low`. On hosts with the CLI status-item API, it also shows the last applied effort.

The optional Desktop extension provides a small effort chip, a details pane and mode controls. Enable it in Desktop's plugin controls after installing the package; if the host does not discover the package's `desktop/` extension, that extension must be deployed to the host's `desktop-plugins/hermes-adaptive-effort/` directory separately. The agent plugin must also be enabled for its backend to work.

The chip follows the focused conversation and backend/profile, including after a completed turn while its result remains cached. It shows `N/A` when there is no usable decision for that chat. Change notifications and the pane's latest transition reflect activity across conversations.

## Compatibility and limits

A reasoning-capable model is not enough: its vendor must accept an effort control on the exact route. `unsupported` with zero probes usually means there is no field to change. OpenCode Go's `space-bunny-free` profile remains outside the verified injection list. `auto` and the retained `inject` mode add fields only for exact routes in the compatibility matrix, or for exact IDs an operator manually lists in `force_injection_models`. That list is an operator assertion, never vendor evidence; it uses no wildcards and works only with known Responses or Chat Completions containers. Generic OpenAI-compatible fallback does not prove route support.

The route vocabulary comes from Hermes plus narrow mappings for Kimi K3, GLM-5.2/5.3 and the exact Muse tiers listed below. Unknown routes use Hermes' broad OpenAI-compatible vocabulary for existing-field rewrites, which cannot guarantee vendor acceptance and never makes a route eligible for injection. **Known gap:** Ox Alpha / `x-preview-f-free` can reject `medium` with HTTP 400. That gap is documented rather than silently remapped.

`cache_safe` treats `chat_completions` and `codex_responses` as cache-neutral, and Anthropic/unknown API modes as cache-hostile. Auto-injected decisions use the same rule and stay pinned even when later requests contain the injected field. These are routing rules, not measured cache-hit guarantees. Session decisions can be evicted from the bounded cache or lost on reload/reset.

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

If you used `jev-auto-effort`, install the new payload, move your old settings from `plugins.entries.jev-auto-effort.settings` to `plugins.entries.hermes-adaptive-effort.settings`, disable the old ID and enable the new one. Replace the old Desktop extension too. Keep your existing mode deliberately, and run only one middleware copy to avoid duplicate scoring. Update source metadata to the renamed repository when using a managed install.

## More documentation

- [Design choices](docs/DESIGN.md): purpose, scope and trade-offs.
- [Runtime contracts](docs/CONTRACTS.md): route mappings, cache scope, failure states and public APIs.
- [Model compatibility](docs/MODEL_COMPATIBILITY.md): exact route evidence and OpenCode Go catalog injection/no-op outcomes.
- [Automated checks](docs/CI.md): Linux/Windows tests, required Hermes integration, security scans and their limits.
- [Development](docs/DEVELOPMENT.md): repository layout, network-free tests and lint commands.
- [Documentation index](docs/README.md): current references and dated review history.
- [Operator handoff](docs/HANDOFF.md): the recorded Iris/Windows rollout and unresolved operational items.

The Windows suite last verified for the prior implementation had 217 passing tests, including real Hermes plugin discovery/dispatcher integration. Tests use fake scorer transports and block network access. See the development guide to reproduce them.

### Exact-route effort injection

The plugin remains globally off by default. In `auto`, missing effort is injected only for positively verified routes or exact model IDs explicitly listed in `force_injection_models`; `inject` applies the same rules with cache-safe routing. The current OpenCode Go registry includes exact Responses models, effort-only Chat Completions models, and paired controls only when the request already enables thinking. Every model published in the current Go catalog has an explicit tested injection/no-op outcome in the [compatibility matrix](docs/MODEL_COMPATIBILITY.md).

| Provider | Exact model IDs | `api_mode` | Wire values |
| --- | --- | --- | --- |
| OpenCode Zen (`opencode-zen`, `opencode`, `opencode_zen`, `zen`) | `muse-spark-1.3`, `muse-spark-1.2`, `muse-spark-1.3-contributor-free` | `codex_responses` | model-tier-specific Muse vocabulary; see matrix |
| OpenCode Go (`opencode-go`, `opencode_go`, `go`, `opencode-go-sub`) | exact catalog entries in the compatibility matrix | `codex_responses`, `chat_completions` | route-specific; includes effort-only and already-enabled paired controls |

Responses injection writes top-level `reasoning.effort`; Chat Completions injection writes top-level `reasoning_effort`. Paired routes require an already present `extra_body.thinking.type="enabled"`; the plugin never adds that toggle. The optional force list accepts exact bare model IDs, comma or newline separated, with an optional provider prefix stripped for matching; it applies to any provider on `codex_responses` or `chat_completions`. It does not authorize Anthropic or unknown API modes, alter cache-safe/recommend/off behavior, or bypass disabled/malformed-control checks. It is an operator assertion, not evidence that the route accepts the field. If the shared cache-safety table marks an injected route unsafe, the decision stays session-pinned while cached, including when a later request already carries the injected field. Injection events use `from: absent`. Cache benefits remain unmeasured, and the plugin cannot catch a downstream provider rejection.
