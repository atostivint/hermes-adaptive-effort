# Catalog submission: bump Hermes Adaptive Effort to 0.4.0

> Submitted as [NousResearch/hermes-agent#142092](https://github.com/NousResearch/hermes-agent/pull/142092) on 2026-10-20 and kept here as the record of what was proposed. The entry file is [entry.yaml](entry.yaml), a copy of the submitted `plugin-catalog/hermes-adaptive-effort.yaml`. The first submission (0.3.1, pinned to `0998a08`) was [NousResearch/hermes-agent#135532](https://github.com/NousResearch/hermes-agent/pull/135532), merged on 2026-10-10; its text is in this file's git history. For a later release, update `sha`, `version` and every pinned URL together and open a new catalog PR (catalog rule 4).

## What does this PR do?

Bumps the `hermes-adaptive-effort` catalog entry from `0.3.1` to `0.4.0`. One data-only file changes. I am the repository owner (admission rule 5).

- Old pin: `0998a083d85ea52fbef38427b44a54d27332358a` (0.3.1)
- New pin: `c84f1389670cdd5ec4607b13bd32c4809878c415`, the [`v0.4.0` tag](https://github.com/atostivint/hermes-adaptive-effort/releases/tag/v0.4.0) commit
- Commit range for review (rule 4): [0998a08...c84f138](https://github.com/atostivint/hermes-adaptive-effort/compare/0998a083d85ea52fbef38427b44a54d27332358a...c84f1389670cdd5ec4607b13bd32c4809878c415) (26 commits)
- Release notes: [CHANGELOG 0.4.0](https://github.com/atostivint/hermes-adaptive-effort/blob/c84f1389670cdd5ec4607b13bd32c4809878c415/CHANGELOG.md#040---2026-10-20)

## Related Issue

None. Follows #135532, which added the entry.

## Type of Change

- [x] ✨ New feature (catalog SHA bump; no change to Hermes code)

## Changes Made

`plugin-catalog/hermes-adaptive-effort.yaml`:

- `sha`, `version`, `docs_url` and the four screenshot URLs move to the new pin.
- `capabilities.provides_hooks` adds `post_api_request` and `api_request_error`, which 0.4.0 registers (see below).
- The `description` disclosure adds the Nace scorer (`console.nace.ai`), the opt-in context settings, the optional continuation call and the per-route cache verdicts kept in plugin data.
- `known_issues` adds one line for the 0.4.0 breaking removal of the custom `chat_completions` scorer format.
- `requires_hermes` stays `">=0.21.6"`: both new hooks are already in `VALID_HOOKS` at the `v0.21.6` tag (`hermes_cli/plugins.py`).

## What changed in the plugin (0.3.1 → 0.4.0)

- **New hooks `post_api_request` / `api_request_error`.** With `dynamic_trial` (default on, only in `auto`/`always` mode, which is opt-in; the plugin still starts `off`), an unlisted cache-safe route that already carries an effort field is trialled: the hooks read the provider's own cache token counters and HTTP status after each request to decide whether changing effort keeps the prompt cache. Only token counts, effort values and request ids are kept in memory; verdicts persist in the plugin's own SQLite file (`route_verdicts`, at most 128 rows). No text is read or stored. Any hook error leaves requests unchanged.
- **New scorer provider: Nace Drex** (`https://console.nace.ai/v1/systemone`, `NACE_API_KEY`). Only used when selected.
- **Opt-in scorer context, off by default:** `dialogue_context_messages` (0-6 earlier user/assistant text messages, 1,200 characters each, never system/developer messages or tool calls/results) and `continuation_downgrade_votes` (a separate yes/no continuation question to the same scorer). With the defaults, every scorer request body is byte-identical to 0.3.1.
- **Local-only behaviour:** `tool_error_escalation` raises effort one level after a repeated tool error, on exact dynamic routes only; it compares a SHA-256 of the normalised error and sends nothing. Also: a scorer circuit breaker, one bounded retry of transient HTTP statuses inside the existing deadline, per-provider default timeouts, and per-conversation mode overrides stored in the plugin's SQLite file (never in config).
- **Breaking:** the custom `chat_completions` scorer format and OpenRouter chat-model scoring are removed. OpenRouter now uses the Decisions API. Affected configurations fail open (effort unchanged) and make no network call.

No new `requires_env`, Python dependencies, tools, shell commands, background processes, telemetry or self-update. Credentials are still read through `agent.secret_scope` and then the environment.

## How to Test

1. `python scripts/validate_plugin_catalog.py plugin-catalog/` passes with this file (`OK: 705 file(s) valid`; the warnings are about other entries).
2. Export the plugin at the pin (`git archive c84f1389670cdd5ec4607b13bd32c4809878c415`) and run `hermes plugins validate <dir>`.
3. `hermes plugins update hermes-adaptive-effort`. The plugin keeps `off` as the default mode.

## Checklist

### Code

- [x] I've read the Contributing Guide and the catalog submission guide
- [x] Conventional Commit title
- [x] I searched for existing PRs; there is no open PR for this entry
- [x] This PR contains only the one catalog file
- [ ] `pytest tests/ -q`: not run, since the PR adds no code; I ran the catalog validator and the plugin validation instead
- [ ] `python scripts/check`: not run, data-only change
- [ ] Tests for my changes: N/A (data-only entry); the plugin's own suite has 1,067 tests and passes in its CI (Ubuntu Python 3.11/3.12/3.14, Windows 3.12)
- [x] Tested on Windows 11

### Documentation & Housekeeping

- [x] Documentation: the plugin README and CHANGELOG at the pinned SHA
- [ ] `cli-config.yaml.example`: N/A
- [ ] `CONTRIBUTING.md` / `AGENTS.md`: N/A

## Validation

At the pinned commit, on a clean `git archive` export, `hermes plugins validate` with Hermes `v0.22.2+2563` (upstream `1f91fb38`) reported **Validation passed**:

- ✓ manifest, manifest fields, config schema, `requires_env`, oauth, loadable (`__init__.py`, `desktop/plugin.js`), python dependencies (none)
- ✓ capability probe, declared tools / **declared hooks** / declared middleware match registrations
- ✓ security scan `safe`, no core override, desktop surface
- ⚠ private core imports (warning): `agent.reasoning_effort`, `gateway.session_context`, `hermes_cli.plugins_state` and the operator-only `scripts/hermes_codex_probe.py`. These are lazy, fail-open imports; a public seam would be welcome for the reasoning-effort clamp.
- ◇ Isolation note: `scripts/hermes_codex_probe.py` patches Hermes module attributes inside its own process only. It is an operator tool that Hermes never loads, as disclosed in #135532.

I did not run `--install-deps` (the plugin declares no Python dependencies) or the validation against Hermes `v0.21.6` itself; that floor relies on the hook list at that tag.

Live validation remains limited, which is why the release is still a pre-release (see `known_issues`). Cost, cache and quality effects of the route trial are unmeasured beyond the single bounded cache miss documented in the README.
