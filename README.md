# Hermes Adaptive Effort

A small Hermes plugin that chooses reasoning effort for each user turn, using an external scorer. It changes an existing effort setting on the model request and lets Hermes handle the rest.

Built for everyday use: one focused job, a quiet interface, and a scorer you can choose. In the committed release, Jev (TypeSafe) is the default; OpenRouter is an explicit alternative with a model you configure. The scorer and the model answering your conversation are separate choices.

The plugin is opt-in and starts **off**. If scoring fails, your original request continues unchanged. It does not switch your conversation model, add tools, or invent a reasoning setting that your route does not expose.

## Install

You need a Hermes version with plugin `llm_request` middleware and session lifecycle hooks. Effort mapping also uses Hermes internals, so compatibility depends on the installed host version. The optional Desktop interface requires Hermes Desktop with the plugin SDK and focused-conversation state support.

Install the payload from this repository, then enable it:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort'
hermes plugins enable hermes-adaptive-effort
```

These commands work in a shell or PowerShell. The `#hermes-adaptive-effort` fragment selects the payload directory, not the repository root. This is a Hermes plugin, not a pip package.

Restart the Hermes agent process that serves your conversations. For an existing gateway installation:

```bash
hermes gateway restart
```

For a local interactive session, exit and launch Hermes again. Desktop connections may use separate agent processes; reconnect or restart the process behind that connection as well.

### Configure the scorer

Provide the selected scorer's key through Hermes' secret scope or the environment available to its agent process:

| Scorer | Credential | Model |
| --- | --- | --- |
| Jev (default) | `TYPESAFE_API_KEY` | Fixed `jev-latest` scoring model |
| OpenRouter | `OPENROUTER_API_KEY` | An explicit `scorer_model` slug |

For a local Jev session, an environment variable can be set before launching Hermes:

```bash
export TYPESAFE_API_KEY="YOUR_KEY"
```

```powershell
$env:TYPESAFE_API_KEY = "YOUR_KEY"
```

For OpenRouter, use `OPENROUTER_API_KEY` instead. These examples are session-local; use your host's secret setup for persistent or service credentials.

Keep credentials out of `config.yaml`. A key in your terminal environment is available only to processes that inherit it; a gateway service needs its own environment or Hermes secret scope.

Settings live in your Hermes home's `config.yaml`. Add them to the existing `plugins` section rather than replacing your other plugin entries:

```yaml
plugins:
  enabled:
    - hermes-adaptive-effort
  entries:
    hermes-adaptive-effort:
      settings:
        mode: recommend
        scorer_provider: jev
```

For OpenRouter, use:

```yaml
plugins:
  enabled:
    - hermes-adaptive-effort
  entries:
    hermes-adaptive-effort:
      settings:
        mode: recommend
        scorer_provider: openrouter
        scorer_model: "YOUR_OPENROUTER_MODEL_SLUG"
```

Replace the placeholder with the model you want to pay for and use as the scorer. The adapter requests JSON output and caps completion output at 32 tokens. A model must return a valid numeric score within the timeout; an invalid answer fails open. No model is silently selected, and OpenRouter never falls back to Jev.

### Check it, then turn it on

In a Hermes conversation:

```text
/hermes-adaptive-effort status
/hermes-adaptive-effort probe What is 2 + 2?
/hermes-adaptive-effort recommend
```

`status` checks settings and reports whether a key is available. `probe` makes one scorer request using only the text you type; it does not classify your conversation or store a decision. Both a probe and normal classification can incur provider charges.

Send a normal message with reasoning enabled on a compatible route, then check `status` again. A `decided` entry shows the score and mapped target. When you want the plugin to apply its decisions:

```text
/hermes-adaptive-effort auto
```

That command applies to future requests in the current process. To keep it after a restart, set `settings.mode: auto` in config or save it through the Desktop settings form.

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

The plugin sends up to 4,000 characters by default to the external scorer. It does not persist prompts or include them in logs or status. Provider retention policies are separate. Subagent goals are held transiently in memory and classified only when the independent `subagent_mode` setting permits it and the main mode is enabled.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `mode` | `off` | Main routing mode |
| `subagent_mode` | `off` | Independent child-agent mode |
| `scorer_provider` | `jev` | `jev` or `openrouter`; no automatic fallback |
| `scorer_model` | empty | Required OpenRouter model slug; ignored by Jev |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint; ignored by OpenRouter |
| `timeout_s` | `3.0` | HTTP timeout for classification |
| `max_turns` | `64` | Bounded decision-cache capacity per process |
| `prompt_chars` | `4000` | Maximum task characters sent to the scorer |

Jev accepts a full route, API base or bare host; `status` shows the effective URL. OpenRouter uses its fixed chat-completions endpoint. Configured scorer failures leave requests unchanged and appear in status.

## A quiet interface

The CLI reports actual applied changes, such as `Effort changed: high -> low`. On hosts with the CLI status-item API, it also shows the last applied effort.

The optional Desktop extension provides a small effort chip, a details pane and mode controls. Enable it in Desktop's plugin controls after installing the package; if the host does not discover the package's `desktop/` extension, that extension must be deployed to the host's `desktop-plugins/hermes-adaptive-effort/` directory separately. The agent plugin must also be enabled for its backend to work.

The chip follows the focused conversation and backend/profile, including after a completed turn while its result remains cached. It shows `N/A` when there is no usable decision for that chat. Change notifications and the pane's latest transition reflect activity across conversations.

## Compatibility and limits

A reasoning-capable model is not enough: its Hermes provider route must expose a writable effort field. `unsupported` with zero probes usually means there is no field to change. This has been observed with OpenCode Go's `space-bunny-free` profile. The plugin deliberately does not add one.

The route vocabulary comes from Hermes plus narrow mappings for Kimi K3 and GLM-5.2/5.3. Unknown routes use Hermes' broad OpenAI-compatible vocabulary, which cannot guarantee vendor acceptance. **Known gap:** Ox Alpha / `x-preview-f-free` can reject `medium` with HTTP 400. That gap is documented rather than silently remapped.

`cache_safe` treats `chat_completions` and `codex_responses` as cache-neutral, and Anthropic/unknown API modes as cache-hostile. These are routing rules, not measured cache-hit guarantees. Session decisions can be evicted from the bounded cache or lost on reload/reset.

Cloudflare Clef support is being developed separately and is not part of this documented release.

No cost saving, cache benefit or answer-quality improvement is claimed as measured. Scoring adds latency and can add cost. OpenRouter's adapter is covered by network-free tests; a live OpenRouter scorer evaluation has not yet been run.

## Why this plugin exists

I wanted a small, unobtrusive plugin that does one job well: choose an appropriate reasoning effort without adding a large layer of features around the agent. I built it for my own daily use and intend to maintain it as I use it.

It started with Jev, but the job should not be tied to one scoring service. The neutral name and explicit scorer selection let the same routing rules work with Jev or a configured OpenRouter model. The interface stays quiet, the controls remain explicit, and a scoring failure lets the conversation continue.

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
- [Development](docs/DEVELOPMENT.md): repository layout, network-free tests and lint commands.
- [Documentation index](docs/README.md): current references and dated review history.
- [Operator handoff](docs/HANDOFF.md): the recorded Iris/Windows rollout and unresolved operational items.

The Windows suite last verified for this implementation has 193 passing tests, including real Hermes plugin discovery/dispatcher integration. Tests use fake scorer transports and block network access. See the development guide to reproduce them.
