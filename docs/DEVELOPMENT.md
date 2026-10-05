# Development

Read [AGENTS.md](../AGENTS.md) for contributor rules, [runtime contracts](CONTRACTS.md) for behavior, and [HANDOFF.md](HANDOFF.md) for the dated operator rollout.

[GitHub Actions](CI.md) runs the suite on Linux and Windows, requires integration with a pinned Hermes host, and adds security checks.

## Windows setup

```powershell
.\scripts\bootstrap_test_env.ps1
.\scripts\run_tests.ps1
.\scripts\run_lint.ps1
```

The Windows test runner discovers the Hermes source tree from `HERMES_SOURCE_ROOT`, `$env:HERMES_HOME\hermes-agent`, a sibling checkout or `$env:LOCALAPPDATA\hermes`. It uses a scratch pytest directory if the usual temporary directory has incompatible permissions. Hermes source must be importable for mapping and dispatcher integration tests.

## Files

```text
./                         the plugin payload installed as ~/.hermes/plugins/hermes-adaptive-effort
  plugin.yaml             manifest: id, commands, hooks, settings defaults + config_schema (Desktop form)
  __init__.py              register(): /hae command and llm_request middleware
  middleware.py            settings, decision cache, request rewrite, session state + effort-change feed
  effort.py                pure score -> label -> wire-effort mapping (no I/O)
  jev_client.py            Jev adapter; openrouter_client.py is the OpenRouter adapter
  cloudflare_client.py     Cloudflare Clef adapter
  custom_client.py         custom System One / OpenAI Chat Completions adapter
  rubric.py                shared scorer question and score validation
  scorers.py               explicit scorer registry, credential and endpoint selection
  cache_safety.py          is an effort change cache-neutral on this route?
  command.py               /hae: help, status, probe, mode verbs
  dashboard/               backend: GET /status, GET /changes, POST /mode, POST /probe
  desktop/plugin.js        desktop half: effort chip, pane, change toasts, palette (opt-in)
tests/                    380 tests, one module per contract
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh (+ .ps1 for Windows)
pytest.ini                pytest configuration
ruff.toml                 Ruff configuration
requirements-dev.txt      test/lint pins (pytest 9.1.1, ruamel.yaml 0.19.1, ruff 0.16.9)
docs/                     design, runtime contracts, development and historical reviews
```

Test modules, by contract:

| module | tests | contract |
| --- | --- | --- |
| `test_middleware.py` | 36 | settings, scorer selection, gating, rewrites, applied-change feed |
| `test_subagent.py` | 17 | child routing, child goals, inheritance |
| `test_jev_client.py` | 18 | transport, credential probe, failure modes, endpoint normalization |
| `test_command.py` | 23 | `/hae` rendering, schemas, scorer/route identity, no prompt leak |
| `test_cache_safety.py` | 11 | cache-neutral vs cache-hostile routes |
| `test_command_modes.py` | 10 | the `off`/`recommend`/`auto`/`cache_safe` verbs |
| `test_turn_scope.py` | 14 | the `(session_id, turn_id)` decision key, current prompt selection with full history |
| `test_review_fixes.py` | 8 | regressions found by the 2026-09-29 review |
| `test_decision_cache.py` | 7 | route-tagged decisions, re-clamp, telemetry |
| `test_effort.py` | 6 | score thresholds, clamping, overrides |
| `test_dispatcher_integration.py` | 6 | through Hermes' own plugin manager + middleware |
| `test_plugin_registration.py` | 4 | manifest, `register()` contract |
| `test_config_schema.py` | 5 | `config_schema` keys/types/defaults and scorer selection match middleware |
| `test_plugin_api.py` | 12 | dashboard backend (status/mode/probe/changes, no prompt leak) + desktop static contract |
| `test_openrouter_client.py` | 15 | bounded OpenRouter request, strict scores, credentials, and fail-open errors |
| `test_scorers.py` | 6 | Jev default and explicit provider selection without cross-provider fallback |
| `test_rubric.py` | 3 | shared effort rubric, fixed score contract and bounded operator guidance |

## Running the tests

```bash
./scripts/run_tests.sh          # the invocation that works, with the interpreter that works
./scripts/run_lint.sh           # ruff, configured in ruff.toml
```

`scripts/run_tests.sh` runs `.venv/bin/python -m pytest tests`, i.e. **the project venv**,
which is the interpreter where the plugin's tests and the Hermes core are both importable
(`hermes_cli` from `/usr/local/lib/hermes-agent`, added to `sys.path` by
`tests/conftest.py`; override with `HERMES_SOURCE_ROOT`). On a fresh machine:

```bash
./scripts/bootstrap_test_env.sh   # creates .venv --system-site-packages, installs
                                  # requirements-dev.txt, then runs the suite
```

The whole suite is **network-free by contract**: `tests/conftest.py` patches
`socket.socket` and `socket.create_connection` for the entire session (`autouse`), so a
test that opens a socket fails instead of silently calling a provider. The same file
makes plugin settings hermetic â€” tests never read `~/.hermes/config.yaml`, so a live
profile cannot change the default-mode assertions.

### The integration test is real, and it runs

`tests/test_dispatcher_integration.py` does not call the plugin's callback directly: it
boots a throwaway `HERMES_HOME`, copies the payload into `<home>/plugins/hermes-adaptive-effort`, lets
Hermes' own `PluginManager.discover_and_load()` find and register it, and then enters
through `hermes_cli.middleware.apply_llm_request_middleware` â€” the function
`agent/turn_api_request.py` calls before building a provider request. It covers, in that
real path:

1. a turn is rewritten once and reported as changed;
2. a tool loop (three requests of one turn) reuses that decision: **one** scorer call;
3. a provider fallback inside the turn re-clamps the target to the new route instead of
   replaying a stale level;
4. a classifier failure fails open, costs one probe, and is not retried in the turn;
5. a request with no writable effort field is reported `unsupported` with no scorer call;
6. `off` exercises nothing (no rewrite, no scorer call).

It runs in `./scripts/run_tests.sh`; it is never skipped there.

Cloudflare adds `test_cloudflare_client.py` (19 tests): fixed REST construction, account validation, strict scores, credentials and fail-open errors.
