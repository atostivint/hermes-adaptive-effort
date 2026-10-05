# Draft: add Hermes Adaptive Effort to the plugin catalog

## What it does

Hermes Adaptive Effort asks a separately selected classifier to score the current task and adjusts an existing reasoning-effort field when the selected mode permits it. Jev remains the default; users can instead choose OpenRouter, Cloudflare Clef, or a custom hosted/local System One or Chat Completions endpoint. The classifier does not replace the model that answers the conversation.

This entry pins version `0.2.0` at `3e2bd41f785efc8b57ce058fdbf8c40a79084823`, under `hermes-adaptive-effort/`.

## Hermes surfaces

- `llm_request` middleware
- Session lifecycle hooks: `on_session_end`, `on_session_finalize`, `on_session_reset`, `subagent_start`, and `subagent_stop`
- Optional Desktop plugin SDK settings and status UI

The plugin starts in `off` mode. It changes only an existing effort field and fails open, leaving requests unchanged when configuration, consent, transport, or score validation fails.

## Disclosures

- **Network and prompt data:** after matching provider-specific consent, the plugin sends the latest task text (bounded by `prompt_chars`) to the selected scorer. Destinations may be TypeSafe/Jev, OpenRouter, Cloudflare Workers AI, or the operator's exact custom endpoint, including a local server. The plugin does not persist prompts. OpenRouter requests require ZDR routing; this plugin cannot guarantee provider retention behavior for Jev, Cloudflare, or custom endpoints.
- **Credentials:** the selected provider may read its configured credential through Hermes secret scope or the process environment: `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, `CLOUDFLARE_AUTH_TOKEN`, or `CUSTOM_SCORER_API_KEY`. A local custom endpoint can use `custom_auth: none`. The plugin does not read another application's credential store.
- **Shell/background activity:** normal plugin operation does not launch shell commands or background processes. The repository includes separate Windows scripts for operators who want to download pinned local scorer runtimes/models and run a local benchmark; those are not invoked by plugin installation or request handling.
- **Telemetry:** no usage telemetry is sent.

## Validation

At the pinned commit, the full project suite passes (248 tests), lint passes, and Hermes' underlying plugin validator returned `ok: true` for the payload, including manifest, config schema, capabilities, security scan, Desktop surface, and no-core-override checks. Locally, the Hermes CLI wrapper could not read the existing Hermes home due to an access-denied error, so validation was run through Hermes' `plugin_validate.validate_plugin_dir()` entry point directly. Catalog CI should repeat its own validation at this pin.

## Screenshots required before opening

The optional Desktop settings/status UI is included. Attach real screenshots of the plugin's settings and status UI hosted on GitHub, then populate the catalog entry's `screenshots:` field with URLs pinned to this SHA.
