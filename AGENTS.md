# AGENTS.md — hermes-adaptive-effort

Hermes plugin that lets a selected external rubric scorer pick the **reasoning effort**
of a request. Fail-open by contract: any error leaves the request untouched.

## Layout

```text
./                         plugin payload installed as ~/.hermes/plugins/hermes-adaptive-effort (NOT pip-installable)
  plugin.yaml             manifest: id, commands, hooks, settings defaults
  __init__.py             register(): llm_request middleware + lifecycle hooks + /hae command
  middleware.py           settings, mode, decision cache, request rewrite, applied-change feed, session state
  effort.py               pure route-choice/score -> wire-effort mapping (no I/O, no Hermes import at top level)
  jev_client.py           Jev adapter + credential probe (lazy core import)
  openai_decision_client.py OpenAI Decisions adapter (fixed endpoint, secret-scope key lookup)
  rubric.py               shared choice/score questions, pure rubric and strict response validation
  openrouter_client.py    OpenRouter adapter; requires explicit model + OPENROUTER_API_KEY
  cloudflare_client.py   Cloudflare Clef adapter; requires account ID + CLOUDFLARE_AUTH_TOKEN
  custom_client.py        custom System One / OpenAI chat-completions adapter
  scorers.py              explicit provider registry, credentials and endpoint display
  cache_safety.py         is an effort change cache-neutral on this route?
  command.py              /hae: help, status, status json, probe, mode verbs
  dashboard/              optional Hermes dashboard backend
  desktop/                optional Desktop extension
tests/                    one module per contract (see docs/DEVELOPMENT.md table)
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh
pytest.ini                pytest config; no Python package metadata
ruff.toml                 Ruff config; no Python package metadata
requirements-dev.txt      pytest==9.1.1, ruamel.yaml==0.19.1, ruff==0.16.9
docs/                     design, contracts, development, dated reviews + operator handoff
```

## Commands (use these exactly)

```bash
./scripts/run_tests.sh   # .venv/bin/python -m pytest tests (network-free)
./scripts/run_lint.sh    # .venv/bin/ruff check . (ruff 0.16.9)
./scripts/bootstrap_test_env.sh  # fresh machine: python3 -m venv --system-site-packages .venv + install + test
```

Windows PowerShell equivalents:

```powershell
.\scripts\bootstrap_test_env.ps1
.\scripts\run_tests.ps1
.\scripts\run_lint.ps1
```

- Always use `.venv/bin/python` — it has the Hermes source tree (`agent/`, `hermes_cli/` from `/usr/local/lib/hermes-agent`, `HERMES_SOURCE_ROOT` override) on `sys.path` via `tests/conftest.py`.
- Never `pip install` the payload itself; there is nothing distributable.

## Tool configuration

- `target-version = "py310"`, `line-length = 100`.
- Ruff: `select = ["E","F","W","B"]`, `ignore = ["E501"]`. `I` (import sort) is intentionally OFF — do not reorder imports.
- `E501` ignored: long prose lines (docstrings, usage banner, reason strings) stay unwrapped.
- Pytest: `testpaths = ["tests"]`, `addopts = "-ra"`.
- Keep tool configuration out of `pyproject.toml`: Hermes treats any root `pyproject.toml` as a managed Python runtime member, even without `[project]` dependencies. This plugin runs in Hermes' host runtime and is not a pip package.

## Architecture invariants (do not break)

1. **Default mode is `off` for parent and subagent.** The only public modes are `auto`, `once`, `always`, and `off`. `auto` classifies each new turn only on an exact registered dynamic-effort model/API route with positive cache-safety evidence, including the separately guarded Claude per-message path; elsewhere it retains a decision per session/provider/model/API route. `once` always retains one decision per route. `always` classifies each new user turn. All active modes reuse one decision through a tool loop; a route change within that turn re-clamps the same label. Unknown configured values become `off`; the plugin does not translate legacy mode names or rewrite configuration.
2. **Existing fields are preferred.** Shapes in `middleware._effort_slot`: `extra_body.reasoning.effort`, top-level `reasoning_effort`, top-level `reasoning.effort` (`codex_responses`), and `output_config.effort` only for exact native Anthropic model/API/host registry entries. Every active mode may add a missing field only for exact registry entries, or exact IDs in `effort_models` on known Responses/Chat Completions carriers. `effort_models` is an operator assertion, not proof of route support or dynamic behavior; it cannot authorize Anthropic or unknown API modes. Unknown generic OpenAI-compatible routes are never evidence. Never add a thinking toggle, overwrite malformed/disabled controls, or touch `"none"` / `enabled: false`.
3. **Clamp onto the route vocabulary.** `effort.map_effort` → `agent.reasoning_effort.clamp_effort` + narrow `wire_efforts`/`wire_overrides` for Kimi K3 / GLM-5.2 / GLM-5.3. `openai-codex` skips the narrow table. Unknown routes fall back to the widest OpenAI-compatible set. Ox Alpha is retired; its earlier `medium` → 400 observation remains in dated reports, not current warnings. Do not silently add a remapping for a historical route.
4. **Decision memory is split and bounded.** Turn memo `(session_id, turn_id)` and persistent `(session_id, provider, exact model, api_mode)` entries each have a `max_turns` bound. At most one scorer call per turn even across route changes; `failed`/`unsupported` are not retried in their selected scope; concurrent turn claims use `_IN_FLIGHT` and retained-route claims use `_ROUTE_IN_FLIGHT`; re-clamp the label on route change (`_target_for_route`).
5. **Two explicit classification contracts:** exact registered model/API routes ask the scorer for a named choice from the route's allowed levels; a single-level route is fixed without scoring. Strictly reject refusal, malformed or out-of-list values and fail open without a score retry. `none` and `ultra` are never automatic choices. Unknown vocabularies and `/hae probe` keep the shared finite numeric score `0..2` → `low (<0.5)` / `medium (<1.5)` / `high`; invalid numbers fail open. Jev is the default; OpenAI Decisions uses `OPENAI_API_KEY` and defaults to `gpt-6-luna` when `scorer_model` is empty; OpenRouter and custom require an explicit model, Cloudflare requires a 32-hex account ID. No provider falls back to Jev.
6. **Fail-open everywhere.** `on_llm_request` catches all; missing credential/model / timeout / transport / malformed → unchanged request + `failed` entry. Missing writable or positively supported injection field → `unsupported`; ineligible routes make no scorer call.
7. **Subagents:** child classified from parent-written goal (`subagent_start` hook), gated by independent `subagent_mode`. `status` does not classify; `probe` scores only operator-typed text plus configured classifier guidance and stores no decision.
8. **Enabling a routing mode authorizes scorer prompt sharing.** Selecting a scorer alone while mode is `off` sends no prompt; enabling a mode sends the bounded latest user text plus optional `classification_instructions` and, for native choices, the allowed effort names to the selected scorer. The choice list does not disclose model identity or observed effort. `use_target_model_context` defaults to false; when true, an active request additionally shares bounded target provider/model/API identity, the observed effort value, and any exact-ID local model profile with the selected scorer. `probe` sends only operator-typed text plus configured guidance and never infers a target route; it keeps the numeric score contract. The guidance is capped at 2000 characters and is never exposed in status, logs, reasons, traces, probe output, or the applied-change feed. `prompt_chars` caps only user task text and gives no retention guarantee. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; this plugin cannot assure ZDR for Jev, Cloudflare, or custom endpoints. Prompts never enter logs/reasons/traces; reason strings and the applied-change feed carry effort values only (injection records prior value `absent`). `command._ENTRY_FIELDS` is the only rendered session allowlist.
9. **Never write the operator's config.** `/hae <mode>` sets in-memory `_MODE_OVERRIDE` for future requests in this process only; persist path is `plugins.entries.hermes-adaptive-effort.settings.mode`.
10. **Provider settings:** Jev uses configurable `jev_model` (default `jev_client.JEV_MODEL`), `endpoint`, and `TYPESAFE_API_KEY`; OpenAI Decisions uses the fixed `/v1/decisions` endpoint, optional `scorer_model` (default `gpt-6-luna`), and `OPENAI_API_KEY`; OpenRouter uses `scorer_model`, the fixed chat-completions endpoint, and `OPENROUTER_API_KEY`; Cloudflare uses `cloudflare_account_id`, `cloudflare_model` (`clef` or `clef-flash`), and `CLOUDFLARE_AUTH_TOKEN`; custom uses an exact `custom_endpoint`, `scorer_model`, and `custom_api_format` (`systemone` or `chat_completions`), with optional `CUSTOM_SCORER_API_KEY` bearer auth selected by `custom_auth`. Required keys resolve through `agent.secret_scope` then env; `credential_present()` never returns a secret. Jev endpoint tolerance remains full route/API base/bare host. See invariant 8 for prompt handling.
11. **Dynamic capability is exact.** Cache-neutral API mode alone is insufficient. `_dynamic_effort_route` requires an exact registered model/API pair (current OpenCode Go/Zen control routes and `openai-codex/gpt-6.1-sol`) plus positive transport cache-safety evidence. Anthropic per-message changes are separately limited to five exact native model IDs, HTTPS `api.anthropic.com`, an existing string `anthropic-beta` header that the plugin can extend, a Messages request, and no `thinking.type=between_tools`. The plugin merges `mid-conversation-output-config-2026-07-01`, inserts/replays empty system markers at user-turn boundaries, and keeps the top-level initial effort unchanged. Its bounded history stores levels and message hashes/positions only. Missing anchors, compression, manual initial-setting changes, reset/eviction or errors invalidate continuity and fail open. Without the effective header, `auto` retains a route decision; `always` may use top-level effort and must expose `top_level_cache_may_reset`. `effort_models` and an existing field never grant dynamic status.
12. **Applied-change feed.** `middleware.effort_change_state()` → `{stream_id, events:[{id,from,to,at}], latest}` under schema `hermes-adaptive-effort.changes.v1`: the rewrites that actually reached a request (bounded ring of 64), effort values only, no session ids, no prompt text. Recorded at the single point where a rewritten request is returned — so `off`, `failed`, `unsupported`, no-op turns and a tool loop re-sending the applied value record nothing — and deduplicated on `(decision_key, from, to)` so a route change re-sending the original level does not replay. `reset_state()` mints a new `stream_id` (the sequence restarts); that is the consumer's signal to drop its cursor. Served as `GET /changes`.
13. **Classifier guidance and display settings:** `classification_instructions` supplements but cannot replace either the fixed 0..2 score contract or a route's allowed named choices; it is shared across all scorer adapters. Target model profiles are local, exact-ID reference data; they neither verify transport support nor change effort clamping. The observed effort is context only and must not anchor a score or choice. `show_tui_status` controls the terminal status item; `show_desktop_popup` controls the bottom-right Desktop chip, mode popup, and its notifications. Both display flags default to true and do not affect classification.
14. **Status and Desktop transition:** status and Desktop decision events are v2 and include `decision_type`, allowed `choices`, and `cache_behavior`; named/fixed decisions use `score: null`, `label` for the chosen level and `target` for the applied value. Probe stays v1 and the anonymous applied-change feed stays v1. Desktop readers accept decision event v1 and v2 during transition. Claude per-message events never request native session-selector synchronization because the top-level initial setting must remain stable.

## Test conventions

- `tests/conftest.py` loads payload as `hermes_plugin_adaptive_effort.<stem>` via `import_plugin()`; Hermes core added to `sys.path` once (`ensure_hermes_source_on_path`).
- `no_network` (session autouse): any `socket.socket` / `create_connection` fails the run. Inject fakes via `_classifier_factory` or `transport=` / `key_reader=`, never real HTTP.
- `hermetic_plugin_settings` (function autouse): `_config_reader = lambda: {}`, `_settings_provider = None`, `_classifier_factory = None`, `reset_state()` before/after. Never read `~/.hermes/config.yaml` in unit tests.
- `test_dispatcher_integration.py` boots a throwaway `HERMES_HOME` + real `PluginManager.discover_and_load()` + `apply_llm_request_middleware`; it runs in `run_tests.sh` and `run_tests.ps1`, never skipped there. `run_tests.ps1` locates the Hermes source tree (`HERMES_SOURCE_ROOT`, then `$env:HERMES_HOME\hermes-agent`, a sibling `hermes-agent/` checkout, `$env:LOCALAPPDATA\hermes`) and falls back to a scratch `--basetemp` when `%TEMP%\pytest-of-<user>` or `.pytest_cache` has a foreign ACL — without either, ~40 mapping tests and every `tmp_path` test error out for unrelated-looking reasons.
- Settings under test live in `middleware.DEFAULTS` (`mode=off`, `subagent_mode=off`, `effort_models=""`, `endpoint=https://api.typesafe.ai/v1/systemone`, `scorer_provider=jev`, `jev_model=jev-latest`, `scorer_model=""`, `custom_endpoint=""`, `custom_api_format=systemone`, `custom_auth=none`, `cloudflare_account_id=""`, `cloudflare_model=clef`, `timeout_s=3.0`, `max_turns=64`, `prompt_chars=4000`, `classification_instructions=""`, `use_target_model_context=false`, `show_tui_status=true`, `show_desktop_popup=true`); OpenAI Decisions uses `gpt-6-luna` when the shared `scorer_model` is blank.

## Hermes plugin development (canonical)

- Locations: `~/.hermes/plugins/<name>/`, `./.hermes/plugins/` (opt-in), `<repo>/plugins/`, pip `hermes_agent.plugins`. This repo is a standalone example.
- Minimal: `plugin.yaml` (`name, version, provides_tools/hooks`) + `__init__.py` with `def register(ctx)`. See `__init__.py:16`.
- `ctx` (`hermes_cli/plugins.py:231`): `register_tool(name, toolset, schema, handler)` — handler `(args:dict, **kwargs)->str` JSON, never raise; `register_hook(name, fn)` — `VALID_HOOKS` (`plugins.py:109`); `register_middleware(kind, fn)` — `VALID_MIDDLEWARE` (`middleware.py:24`: `llm_request/tool_request/llm_execution/tool_execution`); `register_command` (`/name`) / `register_cli_command` (`hermes <name>`); `get_config/set_config` (only `plugins.entries.<id>.settings`), `ctx.state`, `dispatch_tool`, `register_skill/locale`.
- Hooks take `**kwargs` (additive payloads); middleware is fail-open, request returns `{"request"|"args":...}`, execution calls `next_call` exactly once. Order/contract: `website/docs/developer-guide/middleware.md`, guide: `website/docs/developer-guide/plugins/index.md`, policy: `plugins/AGENTS.md`.
- Rules: never touch core files; internal `agent.*`/`hermes_cli.*` imports are not API (lazy import); secrets in `.env`/`secret_scope`, never `config.yaml`; no `~/.hermes` hardcode (`get_hermes_home()`); third-party-product plugins stay out-of-tree.
- Enable/debug: `hermes plugins enable <name>`, `list`, `doctor . --ci`, `validate`; `HERMES_PLUGINS_DEBUG=1` for discovery trace. Test via real discovery with temp `HERMES_HOME`.

## When editing

- Keep `effort.py` pure (stdlib only; lazy `agent.*` imports inside functions).
- Keep existing failure reason codes stable; additive scorer codes include `model_missing`, `account_missing`, `account_invalid`, `unsupported_provider`, `endpoint_missing`, `endpoint_invalid`, `unsupported_api_format`, and `unsupported_auth`. The status contract also exposes `credential_required` — status schema `hermes-adaptive-effort.status.v2`, probe schema `hermes-adaptive-effort.probe.v1`, and Desktop decision event schema v2 are documented contracts. The probe and anonymous change-feed schemas do not change.
- Unknown `/hae` verb or stray arg → return `USAGE`, change nothing.
- Update `README.md`, `docs/CONTRACTS.md` + `docs/HANDOFF.md` if behavior changes; note cost/cache claims as unmeasured unless you run a live A/B.
- Add/update `test_named_effort_choices.py` and `test_native_choice_middleware.py` when changing named-choice protocols, route vocabularies, or Anthropic marker continuity; retain score-contract tests for probes and unknown vocabularies.
