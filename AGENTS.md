# AGENTS.md — jev-auto-effort

Hermes plugin that lets an external rubric scorer (Jev) pick the **reasoning effort**
of a request. Fail-open by contract: any error leaves the request untouched.

## Layout

```text
jev-auto-effort/          payload installed as ~/.hermes/plugins/jev-auto-effort (NOT pip-installable)
  plugin.yaml             manifest: id, commands, hooks, settings defaults
  __init__.py             register(): llm_request middleware + on_session_end/subagent hooks + /jev-auto-effort
  middleware.py           settings, mode, decision cache, request rewrite, session state
  effort.py               pure score -> label -> wire-effort mapping (no I/O, no Hermes import at top level)
  jev_client.py           HTTP client for scorer + credential probe (lazy core import)
  cache_safety.py         is an effort change cache-neutral on this route?
  command.py              /jev-auto-effort: help, status, status json, probe, mode verbs
tests/                    one module per contract (see README.md table)
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh
pyproject.toml            pytest + ruff config only — no [project] table on purpose
requirements-dev.txt      pytest==9.1.1, ruamel.yaml==0.19.1, ruff==0.16.9
docs/                     reviews + handoff for card t_cb5d47d0
```

## Commands (use these exactly)

```bash
./scripts/run_tests.sh   # .venv/bin/python -m pytest tests (~127 tests, ~8s, network-free)
./scripts/run_lint.sh    # .venv/bin/ruff check . (ruff 0.16.9)
./scripts/bootstrap_test_env.sh  # fresh machine: python3 -m venv --system-site-packages .venv + install + test
```

- Always use `.venv/bin/python` — it has the Hermes source tree (`agent/`, `hermes_cli/` from `/usr/local/lib/hermes-agent`, `HERMES_SOURCE_ROOT` override) on `sys.path` via `tests/conftest.py`.
- Never `pip install` the payload itself; there is nothing distributable.

## Config: pyproject.toml

- `target-version = "py310"`, `line-length = 100`.
- Ruff: `select = ["E","F","W","B"]`, `ignore = ["E501"]`. `I` (import sort) is intentionally OFF — do not reorder imports.
- `E501` ignored: long prose lines (docstrings, usage banner, reason strings) stay unwrapped.
- Pytest: `testpaths = ["tests"]`, `addopts = "-ra"`.

## Architecture invariants (do not break)

1. **Default mode is `off`.** Modes: `off` (no-op) / `recommend` (classify, rewrite nothing) / `auto` (rewrite existing field) / `cache_safe` (per-turn on cache-safe routes, session-pinned otherwise).
2. **Rewrite only an existing effort field.** Shapes in `middleware._effort_slot`: `extra_body.reasoning.effort`, top-level `reasoning_effort`, top-level `reasoning.effort` (codex_responses). Never invent a field, never re-enable thinking, never touch `"none"` / `enabled: false`.
3. **Clamp onto the route vocabulary.** `effort.map_effort` → `agent.reasoning_effort.clamp_effort` + narrow `wire_efforts`/`wire_overrides` for Kimi K3 / GLM-5.2 / GLM-5.3. `openai-codex` skips the narrow table. Unknown routes fall back to the widest OpenAI-compatible set. Known gap: Ox Alpha `medium` → 400 (do NOT silently work around; see README "Residual risk").
4. **One Jev call per turn.** Memo key `(session_id, turn_id)`; `failed`/`unsupported` not retried in-turn; concurrent probes claimed via `_IN_FLIGHT`; re-clamp stored target on route change (`_target_for_route`).
5. **Score rubric:** Jev score `0..2` → `low (<0.5)` / `medium (<1.5)` / `high`. Out-of-range, NaN/inf, bool, non-numeric → `None` → fail open.
6. **Fail-open everywhere.** `on_llm_request` catches all; missing credential / timeout / transport / malformed → unchanged request + `failed` entry. Missing writable field → `unsupported`, 0 Jev calls.
7. **Subagents:** child classified from parent-written goal (`subagent_start` hook), gated by independent `subagent_mode`. `probe`/`status` classify/store nothing; `probe` only scores operator-typed text.
8. **No prompt storage, no prompt in logs/reasons/traces.** Reason strings carry effort values only. `command._ENTRY_FIELDS` is the only rendered session allowlist.
9. **Never write the operator's config.** `/jev-auto-effort <mode>` sets in-memory `_MODE_OVERRIDE` for future requests in this process only; persist path is `plugins.entries.jev-auto-effort.settings.mode`.
10. **Endpoint tolerance:** `jev_client.normalize_endpoint` accepts full route, API base, or bare host; `status` shows `endpoint_effective` + raw setting when they differ. Credential `TYPESAFE_API_KEY` via `agent.secret_scope` then env; `credential_present()` never returns the secret.
11. **Cache safety:** `cache_safety.effort_is_cache_safe(provider, model, api_mode)` — `True` only for `chat_completions` / `codex_responses`; `anthropic_messages` and unknown → `False` (pin, never gamble).

## Test conventions

- `tests/conftest.py` loads payload as `hermes_plugin_jev_auto.<stem>` via `import_plugin()`; Hermes core added to `sys.path` once (`ensure_hermes_source_on_path`).
- `no_network` (session autouse): any `socket.socket` / `create_connection` fails the run. Inject fakes via `_classifier_factory` or `transport=` / `key_reader=`, never real HTTP.
- `hermetic_plugin_settings` (function autouse): `_config_reader = lambda: {}`, `_settings_provider = None`, `_classifier_factory = None`, `reset_state()` before/after. Never read `~/.hermes/config.yaml` in unit tests.
- `test_dispatcher_integration.py` boots a throwaway `HERMES_HOME` + real `PluginManager.discover_and_load()` + `apply_llm_request_middleware`; it runs in `run_tests.sh`, never skipped there.
- Settings under test live in `middleware.DEFAULTS` (`mode=off`, `subagent_mode=off`, `endpoint=https://api.typesafe.ai/v1/systemone`, `timeout_s=3.0`, `max_turns=64`, `prompt_chars=4000`); scorer model is const `jev_client.JEV_MODEL`, not a setting.

## Hermes plugin development (canonical)

- Locations: `~/.hermes/plugins/<name>/`, `./.hermes/plugins/` (opt-in), `<repo>/plugins/`, pip `hermes_agent.plugins`. This repo is a standalone example.
- Minimal: `plugin.yaml` (`name, version, provides_tools/hooks`) + `__init__.py` with `def register(ctx)`. See `jev-auto-effort/__init__.py:16`.
- `ctx` (`hermes_cli/plugins.py:231`): `register_tool(name, toolset, schema, handler)` — handler `(args:dict, **kwargs)->str` JSON, never raise; `register_hook(name, fn)` — `VALID_HOOKS` (`plugins.py:109`); `register_middleware(kind, fn)` — `VALID_MIDDLEWARE` (`middleware.py:24`: `llm_request/tool_request/llm_execution/tool_execution`); `register_command` (`/name`) / `register_cli_command` (`hermes <name>`); `get_config/set_config` (only `plugins.entries.<id>.settings`), `ctx.state`, `dispatch_tool`, `register_skill/locale`.
- Hooks take `**kwargs` (additive payloads); middleware is fail-open, request returns `{"request"|"args":...}`, execution calls `next_call` exactly once. Order/contract: `website/docs/developer-guide/middleware.md`, guide: `website/docs/developer-guide/plugins/index.md`, policy: `plugins/AGENTS.md`.
- Rules: never touch core files; internal `agent.*`/`hermes_cli.*` imports are not API (lazy import); secrets in `.env`/`secret_scope`, never `config.yaml`; no `~/.hermes` hardcode (`get_hermes_home()`); third-party-product plugins stay out-of-tree.
- Enable/debug: `hermes plugins enable <name>`, `list`, `doctor . --ci`, `validate`; `HERMES_PLUGINS_DEBUG=1` for discovery trace. Test via real discovery with temp `HERMES_HOME`.

## When editing

- Keep `effort.py` pure (stdlib only; lazy `agent.*` imports inside functions).
- Keep failure reason codes stable (`invalid_prompt`, `credential_missing`, `http_error`, `timeout`, `transport_error`, `malformed_response`, `unexpected_error`, `classifier_error`) — `status`/`status json` schemas (`jev-auto-effort.status.v1`, `jev-auto-effort.probe.v1`) are a documented contract.
- Unknown `/jev-auto-effort` verb or stray arg → return `USAGE`, change nothing.
- Update `README.md` contract tables + `docs/` handoff if behavior changes; note cost/cache claims as unmeasured unless you run a live A/B.
