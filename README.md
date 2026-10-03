# Hermes Adaptive Effort

[![CI](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/ci.yml) [![Security](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml/badge.svg)](https://github.com/atostivint/hermes-adaptive-effort/actions/workflows/security.yml)

A small Hermes plugin that chooses reasoning effort for each user turn, using an external scorer. It changes an existing effort setting on the model request and lets Hermes handle the rest.

Built for everyday use: one focused job, a quiet interface, and a scorer you can choose. In the committed release, Jev (TypeSafe) is the default; OpenRouter is an explicit alternative with a model you configure. The scorer and the model answering your conversation are separate choices.

The plugin is opt-in and starts **off**. If scoring fails, your original request continues unchanged. It does not switch your conversation model, add tools, or invent a reasoning setting that your route does not expose.

## Quick install

Already have Hermes? Install and enable the plugin with one command, in a shell or PowerShell:

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

### 3. Choose a mode in chat

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

In the plugin's Desktop settings, select `openrouter` and enter your scorer model slug. Make `OPENROUTER_API_KEY` available to the serving Hermes process, then use the same chat commands above. No YAML editing is needed when your Desktop host exposes the settings form.

The scorer is separate from your conversation model. You choose which OpenRouter model to pay for; no model is silently selected and no failure falls back to Jev. The adapter requests JSON and caps completion output at 32 tokens; invalid answers or timeouts preserve the original request.

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

Replace the model placeholder before using it. Start with `mode: recommend` instead of `auto` if you want to observe decisions first.

| Scorer | Credential | Model |
| --- | --- | --- |
| Jev (default) | `TYPESAFE_API_KEY` | Fixed `jev-latest` |
| OpenRouter | `OPENROUTER_API_KEY` | Explicit `scorer_model` slug |

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
2. Send a bounded excerpt to the selected scorer. A finite score from `0` to `2` becomes `low` below `0.5`, `medium` below `1.5`, or `high` otherwise.
3. Map that label to the route's available effort values.
4. Rewrite only an existing `reasoning.effort` or `reasoning_effort` slot. Explicitly disabled reasoning stays disabled.

A tool loop reuses its turn's decision instead of calling the scorer for every model request. A new user turn gets a new classification in `auto`. A new classification does not necessarily change the effort: the existing value may match, or a route may offer only a narrow set of levels.

Scoring sends prompt text outside the client, so it requires an explicit provider-specific opt-in: set `prompt_sharing_provider` to the selected scorer. With no matching opt-in, the plugin makes no scorer request and fails open with `prompt_consent_required`. The `prompt_chars` setting caps the text sent (4,000 characters by default); truncation does not guarantee provider non-retention. The plugin itself does not persist prompts or include them in logs or status. Provider retention policies are separate. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; this does not keep the prompt from being processed by OpenRouter. This plugin cannot assure ZDR for Jev or Cloudflare. Subagent goals are held transiently in memory and classified only when the independent `subagent_mode` setting permits it and the main mode is enabled; child scoring follows the same provider opt-in gate.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `mode` | `off` | Main routing mode |
| `subagent_mode` | `off` | Independent child-agent mode |
| `scorer_provider` | `jev` | `jev`, `openrouter` or `cloudflare`; no automatic fallback |
| `scorer_model` | empty | Required OpenRouter model slug; ignored by Jev and Cloudflare |
| `cloudflare_account_id` | empty | Required 32-character hexadecimal account ID for Cloudflare |
| `prompt_sharing_provider` | `none` | Explicitly permit sending prompt text to this scorer (`none`, `jev`, `openrouter` or `cloudflare`); must match `scorer_provider` |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint; ignored by OpenRouter and Cloudflare |
| `timeout_s` | `3.0` | HTTP timeout for classification |
| `max_turns` | `64` | Bounded decision-cache capacity per process |
| `prompt_chars` | `4000` | Maximum task characters sent after matching provider consent; not a retention control |

Jev accepts a full route, API base or bare host; `status` shows the effective URL. OpenRouter uses its fixed chat-completions endpoint and requests `provider.zdr=true` plus `data_collection=deny`; if no eligible route is available the request fails open rather than using a non-ZDR endpoint. Cloudflare uses Workers AI's account-scoped `@cf/cloudflare/clef` REST route and fixed `clef` model; it requires `CLOUDFLARE_AUTH_TOKEN` and a valid account ID. Status reports token presence and account readiness without exposing the token. Configured scorer failures leave requests unchanged and appear in status.

Cloudflare Clef receives the same bounded `state.prompt` and score question as Jev. Its REST response must have `success: true` and a finite numeric `result.answers.effort.score` from 0 through 2. See the [Clef model documentation](https://developers.cloudflare.com/workers-ai/models/clef/) and [Workers AI REST API guide](https://developers.cloudflare.com/workers-ai/get-started/rest-api/).

Cloudflare configuration failures use `account_missing` or `account_invalid`; transport and malformed response failures use the shared fail-open reason codes.

## A quiet interface

The CLI reports actual applied changes, such as `Effort changed: high -> low`. On hosts with the CLI status-item API, it also shows the last applied effort.

The optional Desktop extension provides a small effort chip, a details pane and mode controls. Enable it in Desktop's plugin controls after installing the package; if the host does not discover the package's `desktop/` extension, that extension must be deployed to the host's `desktop-plugins/hermes-adaptive-effort/` directory separately. The agent plugin must also be enabled for its backend to work.

The chip follows the focused conversation and backend/profile, including after a completed turn while its result remains cached. It shows `N/A` when there is no usable decision for that chat. Change notifications and the pane's latest transition reflect activity across conversations.

## Compatibility and limits

A reasoning-capable model is not enough: its Hermes provider route must expose a writable effort field. `unsupported` with zero probes usually means there is no field to change. This has been observed with OpenCode Go's `space-bunny-free` profile. The plugin deliberately does not add one.

The route vocabulary comes from Hermes plus narrow mappings for Kimi K3 and GLM-5.2/5.3. Unknown routes use Hermes' broad OpenAI-compatible vocabulary, which cannot guarantee vendor acceptance. **Known gap:** Ox Alpha / `x-preview-f-free` can reject `medium` with HTTP 400. That gap is documented rather than silently remapped.

`cache_safe` treats `chat_completions` and `codex_responses` as cache-neutral, and Anthropic/unknown API modes as cache-hostile. These are routing rules, not measured cache-hit guarantees. Session decisions can be evicted from the bounded cache or lost on reload/reset.

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

Restart the serving agent after an update or enable/disable change. `/hermes-adaptive-effort off` stops routing immediately for future requests in that process; set the persistent mode to `off` if it should stay off.

If you used `jev-auto-effort`, install the new payload, move your old settings from `plugins.entries.jev-auto-effort.settings` to `plugins.entries.hermes-adaptive-effort.settings`, disable the old ID and enable the new one. Replace the old Desktop extension too. Keep your existing mode deliberately, and run only one middleware copy to avoid duplicate scoring. Update source metadata to the renamed repository when using a managed install.

## More documentation

- [Design choices](docs/DESIGN.md): purpose, scope and trade-offs.
- [Runtime contracts](docs/CONTRACTS.md): route mappings, cache scope, failure states and public APIs.
- [Automated checks](docs/CI.md): Linux/Windows tests, required Hermes integration, security scans and their limits.
- [Development](docs/DEVELOPMENT.md): repository layout, network-free tests and lint commands.
- [Documentation index](docs/README.md): current references and dated review history.
- [Operator handoff](docs/HANDOFF.md): the recorded Iris/Windows rollout and unresolved operational items.

The Windows suite last verified for this implementation has 217 passing tests, including real Hermes plugin discovery/dispatcher integration. Tests use fake scorer transports and block network access. See the development guide to reproduce them.
