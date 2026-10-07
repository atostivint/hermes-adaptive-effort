# Draft: add Hermes Adaptive Effort to the plugin catalog

> Submission draft for `NousResearch/hermes-agent`, pinned to the v0.3.0 pre-release. The entry file is [entry.yaml](entry.yaml); copy it to `plugin-catalog/hermes-adaptive-effort.yaml`. Recheck the pin and the validation lines below if a newer release is chosen before opening the PR.

## What it does

Hermes Adaptive Effort asks a separately selected scorer to judge the current task, then sets the reasoning effort of the outgoing request when the selected mode allows it. Jev is the default scorer; users can instead choose OpenAI Decisions, an OpenRouter model, Cloudflare Clef / Clef Flash, or a custom hosted or local endpoint (System One or Chat Completions format). The scorer does not replace the model that answers the conversation.

On exact registered routes the scorer picks from that route's own effort levels; other routes use a numeric `0..2` score mapped to `low/medium/high`. The plugin starts in `off` mode for both parent and child sessions and fails open: configuration, transport, timeout or validation failures leave the request unchanged.

This entry pins version `0.3.0` (a GitHub pre-release) at `8b5381727bbe381becf051991aaf07768f832cef`. The payload is the repository root, so the entry has no `subdir`.

The maintainer is the repository owner. The idea follows Alexei Ledenev's Jev-based model router for the Pi agent; this plugin is a separate implementation for Hermes, not a fork of a catalog entry.

## Hermes surfaces

- `llm_request` middleware
- Session lifecycle hooks: `on_session_end`, `on_session_finalize`, `on_session_reset`, `subagent_start`, `subagent_stop`
- `/hae` chat command (modes, status, history, probe)
- Optional Dashboard routes: `GET /status`, `GET /changes`, `GET /history`, `POST /mode`, `POST /probe`
- Optional Desktop extension (`desktop/plugin.js`) using the plugin SDK: effort chip, routing-mode popup, focused-chat history

## Disclosures

- **Network and prompt data:** enabling a routing mode authorizes sending the latest user task text (at most `prompt_chars`, default 4,000 characters), optional operator guidance and the route's allowed effort level names to the selected scorer. Target model identity and observed effort are added only when `use_target_model_context` is enabled. `/hae probe <text>` sends the operator-typed text even while routing is off. Destinations: TypeSafe (Jev), OpenAI, OpenRouter, Cloudflare Workers AI, or the operator's exact custom endpoint, including a local server. OpenRouter requests ask for ZDR-only routing with data collection denied; the plugin cannot guarantee retention behavior for the other providers.
- **Request changes:** the plugin rewrites a supported effort field and, on exact registered routes, can add a missing one. On exact native Anthropic routes with an already exposed `anthropic-beta` header, it appends Anthropic's dated per-message effort beta value to that header and inserts effort marker messages before matching user turns. Explicitly disabled or malformed reasoning controls are left untouched.
- **Local storage:** a bounded SQLite history in the plugin's Hermes data directory (`plugin-data/hermes-adaptive-effort/effort-history.sqlite3`) records applied effort changes: from/to levels, model names, cache verdict and allowlisted decision metadata, at most 64 changes per conversation and 64 conversations. To follow one conversation across ID rotations it also keeps lookup aliases, including `HERMES_SESSION_KEY` when present. It never stores prompt or task text; a conversation reset clears that conversation. Persisting a mode from the Dashboard or Desktop writes `plugins.entries.hermes-adaptive-effort.settings.mode` through Hermes' settings API.
- **Credentials:** the selected scorer reads only its own credential, through Hermes secret scope first and then the process environment: `TYPESAFE_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `CLOUDFLARE_AUTH_TOKEN` or `CUSTOM_SCORER_API_KEY`. A local custom endpoint can use `custom_auth: none`. No other application's credential store is read.
- **Shell/background activity:** normal operation launches no shell commands or background processes. The repository's `scripts/` directory holds operator tools (local scorer setup and benchmark, model-validation runner, Codex probe); installation and request handling never invoke them.
- **Self-update:** none. Updates arrive only through `hermes plugins update` at a new pin.
- **Telemetry:** none. Desktop decision events are broadcast locally to the Desktop extension and contain no prompt text.

## Validation

At the pinned commit:

- `hermes_cli.plugin_validate.validate_plugin_dir()` on a clean `git archive` export passed all checks: manifest, config schema, `requires_env`, loadable entries, capability probe, declared tools/hooks/middleware, security scan (`safe`), no core override and desktop surface.
- `scripts/validate_plugin_catalog.py` reported the entry file valid.
- The project's GitHub CI passed (Python 3.11, 3.12 and 3.14 on Ubuntu, 3.12 on Windows, Node Desktop scenarios, Ruff) along with CodeQL and dependency/secret checks.

Catalog CI should repeat its own validation at this pin.

Live validation is limited and the release is marked as a pre-release. Live checks used deterministic local scores rather than a real scorer; only `gpt-6.1-sol` showed `low`, `medium` and `high` accepted end to end, other routes are partial or untested, and no cost, cache or quality gain has been measured. The [v0.3.0 release notes](https://github.com/atostivint/hermes-adaptive-effort/releases/tag/v0.3.0) give the per-route table. The entry's `known_issues` line repeats this at install time.

## Screenshots

The entry links one pinned screenshot of the Desktop routing-mode popup: `docs/images/routing-mode-popover.png` at the pinned SHA. Add a settings-panel capture if reviewers want one; it must be committed to the repository first so it can be pinned.
