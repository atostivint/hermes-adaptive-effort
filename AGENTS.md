# AGENTS.md — hermes-adaptive-effort

Hermes plugin that lets a selected external rubric scorer pick the **reasoning effort**
of a request. Fail-open by contract: any error leaves the request untouched.

## Layout

```text
hermes-adaptive-effort/          payload installed as ~/.hermes/plugins/hermes-adaptive-effort (NOT pip-installable)
  plugin.yaml             manifest: id, commands, hooks, settings defaults
  __init__.py             register(): llm_request middleware + on_session_end/subagent hooks + /hermes-adaptive-effort
  middleware.py           settings, mode, decision cache, request rewrite, applied-change feed, session state
  effort.py               pure score -> label -> wire-effort mapping (no I/O, no Hermes import at top level)
  jev_client.py           Jev adapter + credential probe (lazy core import)
  rubric.py               shared, pure score rubric and validation
  openrouter_client.py    OpenRouter adapter; requires explicit model + OPENROUTER_API_KEY
  cloudflare_client.py   Cloudflare Clef adapter; requires account ID + CLOUDFLARE_AUTH_TOKEN
  custom_client.py        custom System One / OpenAI chat-completions adapter
  rubric.py               shared score question and strict JSON score parsing
  scorers.py              explicit provider registry, credentials and endpoint display
  cache_safety.py         is an effort change cache-neutral on this route?
  command.py              /hermes-adaptive-effort: help, status, status json, probe, mode verbs
tests/                    one module per contract (see docs/DEVELOPMENT.md table)
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh
pyproject.toml            pytest + ruff config only — no [project] table on purpose
requirements-dev.txt      pytest==9.1.1, ruamel.yaml==0.19.1, ruff==0.16.9
docs/                     design, contracts, development, dated reviews + operator handoff
```

## Commands (use these exactly)

```bash
./scripts/run_tests.sh   # .venv/bin/python -m pytest tests (~248 tests, ~1s, network-free)
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

## Config: pyproject.toml

- `target-version = "py310"`, `line-length = 100`.
- Ruff: `select = ["E","F","W","B"]`, `ignore = ["E501"]`. `I` (import sort) is intentionally OFF — do not reorder imports.
- `E501` ignored: long prose lines (docstrings, usage banner, reason strings) stay unwrapped.
- Pytest: `testpaths = ["tests"]`, `addopts = "-ra"`.

## Architecture invariants (do not break)

1. **Default mode is `off`.** Modes: `off` (no-op) / `recommend` (classify, rewrite nothing) / `auto` (rewrite existing fields and inject on exact verified or operator-listed models) / `cache_safe` (per-turn on cache-safe routes, session-pinned otherwise) / `inject` (compatibility cache-safe injection mode).
2. **Existing fields are preferred.** Shapes in `middleware._effort_slot`: `extra_body.reasoning.effort`, top-level `reasoning_effort`, top-level `reasoning.effort` (codex_responses). Missing fields may be added only for exact registry entries, or exact IDs in `force_injection_models`, and only to known Responses/Chat Completions carriers. The force list is an operator assertion, not proof; it cannot authorize Anthropic or unknown API modes. Unknown generic OpenAI-compatible routes are never evidence. Never add a thinking toggle, overwrite malformed/disabled controls, or touch `"none"` / `enabled: false`.
3. **Clamp onto the route vocabulary.** `effort.map_effort` → `agent.reasoning_effort.clamp_effort` + narrow `wire_efforts`/`wire_overrides` for Kimi K3 / GLM-5.2 / GLM-5.3. `openai-codex` skips the narrow table. Unknown routes fall back to the widest OpenAI-compatible set. Known gap: Ox Alpha `medium` → 400 (do NOT silently work around; see README "Residual risk").
4. **One selected-scorer call per turn.** Memo key `(session_id, turn_id)`; `failed`/`unsupported` not retried in-turn; concurrent probes claimed via `_IN_FLIGHT`; re-clamp stored target on route change (`_target_for_route`).
5. **Shared score rubric:** finite numeric score `0..2` → `low (<0.5)` / `medium (<1.5)` / `high`. Out-of-range, NaN/inf, bool, non-numeric → `None` → fail open. Jev is the default; OpenRouter and custom require an explicit model, Cloudflare requires a 32-hex account ID. No provider falls back to Jev.
6. **Fail-open everywhere.** `on_llm_request` catches all; missing credential/model / timeout / transport / malformed → unchanged request + `failed` entry. Missing writable or positively supported injection field → `unsupported`; ineligible routes make no scorer call.
7. **Subagents:** child classified from parent-written goal (`subagent_start` hook), gated by independent `subagent_mode`. `status` does not classify; `probe` scores only operator-typed text and stores no decision.
8. **Enabling a routing mode authorizes scorer prompt sharing.** Selecting a scorer alone while mode is `off` sends no prompt; enabling a mode sends task text to the selected scorer. `probe` sends only operator-typed text. `prompt_chars` is only a text-size cap and gives no retention guarantee. OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; this plugin cannot assure ZDR for Jev, Cloudflare, or custom endpoints. Prompts never enter logs/reasons/traces; reason strings and the applied-change feed carry effort values only (injection records prior value `absent`). `command._ENTRY_FIELDS` is the only rendered session allowlist.
9. **Never write the operator's config.** `/hermes-adaptive-effort <mode>` sets in-memory `_MODE_OVERRIDE` for future requests in this process only; persist path is `plugins.entries.hermes-adaptive-effort.settings.mode`.
10. **Provider settings:** Jev uses `endpoint`, `TYPESAFE_API_KEY`, and fixed `jev_client.JEV_MODEL`; OpenRouter uses `scorer_model`, the fixed chat-completions endpoint, and `OPENROUTER_API_KEY`; Cloudflare uses `cloudflare_account_id`, `cloudflare_model` (`clef` or `clef-flash`), and `CLOUDFLARE_AUTH_TOKEN`; custom uses an exact `custom_endpoint`, `scorer_model`, and `custom_api_format` (`systemone` or `chat_completions`), with optional `CUSTOM_SCORER_API_KEY` bearer auth selected by `custom_auth`. Required keys resolve through `agent.secret_scope` then env; `credential_present()` never returns a secret. Jev endpoint tolerance remains full route/API base/bare host. See invariant 8 for prompt handling.
11. **Cache safety:** `cache_safety.effort_is_cache_safe(provider, model, api_mode)` — `True` only for `chat_completions` / `codex_responses`; `anthropic_messages` and unknown → `False` (pin, never gamble).
12. **Applied-change feed.** `middleware.effort_change_state()` → `{stream_id, events:[{id,from,to,at}], latest}` under schema `hermes-adaptive-effort.changes.v1`: the rewrites that actually reached a request (bounded ring of 64), effort values only, no session ids, no prompt text. Recorded at the single point where a rewritten request is returned — so `recommend`, `failed`, `unsupported`, no-op turns and a tool loop re-sending the applied value record nothing — and deduplicated on `(decision_key, from, to)` so a route change re-sending the original level does not replay. `reset_state()` mints a new `stream_id` (the sequence restarts); that is the consumer's signal to drop its cursor. Served as `GET /changes`.

## Test conventions

- `tests/conftest.py` loads payload as `hermes_plugin_adaptive_effort.<stem>` via `import_plugin()`; Hermes core added to `sys.path` once (`ensure_hermes_source_on_path`).
- `no_network` (session autouse): any `socket.socket` / `create_connection` fails the run. Inject fakes via `_classifier_factory` or `transport=` / `key_reader=`, never real HTTP.
- `hermetic_plugin_settings` (function autouse): `_config_reader = lambda: {}`, `_settings_provider = None`, `_classifier_factory = None`, `reset_state()` before/after. Never read `~/.hermes/config.yaml` in unit tests.
- `test_dispatcher_integration.py` boots a throwaway `HERMES_HOME` + real `PluginManager.discover_and_load()` + `apply_llm_request_middleware`; it runs in `run_tests.sh` and `run_tests.ps1`, never skipped there. `run_tests.ps1` locates the Hermes source tree (`HERMES_SOURCE_ROOT`, then `$env:HERMES_HOME\hermes-agent`, a sibling `hermes-agent/` checkout, `$env:LOCALAPPDATA\hermes`) and falls back to a scratch `--basetemp` when `%TEMP%\pytest-of-<user>` or `.pytest_cache` has a foreign ACL — without either, ~40 mapping tests and every `tmp_path` test error out for unrelated-looking reasons.
- Settings under test live in `middleware.DEFAULTS` (`mode=off`, `subagent_mode=off`, `force_injection_models=""`, `endpoint=https://api.typesafe.ai/v1/systemone`, `scorer_provider=jev`, `scorer_model=""`, `custom_endpoint=""`, `custom_api_format=systemone`, `custom_auth=none`, `cloudflare_account_id=""`, `cloudflare_model=clef`, `timeout_s=3.0`, `max_turns=64`, `prompt_chars=4000`); Jev's model is fixed, Cloudflare's model is selected, and OpenRouter/custom models are configured.

## Hermes plugin development (canonical)

- Locations: `~/.hermes/plugins/<name>/`, `./.hermes/plugins/` (opt-in), `<repo>/plugins/`, pip `hermes_agent.plugins`. This repo is a standalone example.
- Minimal: `plugin.yaml` (`name, version, provides_tools/hooks`) + `__init__.py` with `def register(ctx)`. See `hermes-adaptive-effort/__init__.py:16`.
- `ctx` (`hermes_cli/plugins.py:231`): `register_tool(name, toolset, schema, handler)` — handler `(args:dict, **kwargs)->str` JSON, never raise; `register_hook(name, fn)` — `VALID_HOOKS` (`plugins.py:109`); `register_middleware(kind, fn)` — `VALID_MIDDLEWARE` (`middleware.py:24`: `llm_request/tool_request/llm_execution/tool_execution`); `register_command` (`/name`) / `register_cli_command` (`hermes <name>`); `get_config/set_config` (only `plugins.entries.<id>.settings`), `ctx.state`, `dispatch_tool`, `register_skill/locale`.
- Hooks take `**kwargs` (additive payloads); middleware is fail-open, request returns `{"request"|"args":...}`, execution calls `next_call` exactly once. Order/contract: `website/docs/developer-guide/middleware.md`, guide: `website/docs/developer-guide/plugins/index.md`, policy: `plugins/AGENTS.md`.
- Rules: never touch core files; internal `agent.*`/`hermes_cli.*` imports are not API (lazy import); secrets in `.env`/`secret_scope`, never `config.yaml`; no `~/.hermes` hardcode (`get_hermes_home()`); third-party-product plugins stay out-of-tree.
- Enable/debug: `hermes plugins enable <name>`, `list`, `doctor . --ci`, `validate`; `HERMES_PLUGINS_DEBUG=1` for discovery trace. Test via real discovery with temp `HERMES_HOME`.

## When editing

- Keep `effort.py` pure (stdlib only; lazy `agent.*` imports inside functions).
- Keep existing failure reason codes stable; additive scorer codes include `model_missing`, `account_missing`, `account_invalid`, `unsupported_provider`, `endpoint_missing`, `endpoint_invalid`, `unsupported_api_format`, and `unsupported_auth`. The status contract also exposes `credential_required` — `status`/`status json` schemas (`hermes-adaptive-effort.status.v1`, `hermes-adaptive-effort.probe.v1`) are documented contracts.
- Unknown `/hermes-adaptive-effort` verb or stray arg → return `USAGE`, change nothing.
- Update `README.md`, `docs/CONTRACTS.md` + `docs/HANDOFF.md` if behavior changes; note cost/cache claims as unmeasured unless you run a live A/B.
