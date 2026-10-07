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
  middleware.py            settings, decision cache, request rewrite, session state + effort-change feed + Desktop events
  effort.py                pure score -> label -> wire-effort mapping (no I/O)
  jev_client.py            Jev adapter; openrouter_client.py is the OpenRouter adapter
  openai_decision_client.py OpenAI Decisions adapter; fixed endpoint and native score rubric
  cloudflare_client.py     Cloudflare Clef adapter
  custom_client.py         custom System One / OpenAI Chat Completions adapter
  rubric.py                shared scorer question and score validation
  model_profiles.py       exact-ID model profile lookup and bounded scorer context
  model_profiles.json     reviewed vendor effort profiles (data-only model coverage)
  scorers.py               explicit scorer registry, credential and endpoint selection
  cache_safety.py          is an effort change cache-neutral on this route?
  command.py               /hae: help, status, probe, mode verbs
  dashboard/               backend: GET /status, GET /changes, POST /mode, POST /probe
  desktop/plugin.js        Desktop chip, live decision state, focused session sync, compact/details popover (opt-in)
tests/                    network-free tests, one module per contract
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh (+ .ps1 for Windows)
pytest.ini                pytest configuration
ruff.toml                 Ruff configuration
requirements-dev.txt      test/lint pins (pytest 9.1.1, ruamel.yaml 0.19.1, ruff 0.16.9)
docs/                     design, runtime contracts, development and historical reviews
```

Test modules, by contract:

| module | tests | contract |
| --- | --- | --- |
| `test_middleware.py` | 163 | settings, scorer selection, gating, rewrites, applied-change feed |
| `test_model_profiles.py` | 10 | exact profile catalog, bounded context, route observations and all scorer transport formats |
| `test_subagent.py` | 18 | child routing, child goals, inheritance |
| `test_jev_client.py` | 19 | transport, model selection, credential probe, failure modes, endpoint normalization |
| `test_command.py` | 26 | `/hae` rendering, schemas, scorer/route identity, no prompt leak |
| `test_cache_safety.py` | 27 | four-mode decisions, dynamic capabilities, route persistence and concurrency |
| `test_command_modes.py` | 17 | the four canonical mode verbs, strict writes, and disabled unknown config values |
| `test_turn_scope.py` | 11 | the `(session_id, turn_id)` decision key, current prompt selection with full history |
| `test_review_fixes.py` | 8 | regressions found by the 2026-09-29 review |
| `test_decision_cache.py` | 8 | route-tagged decisions, re-clamp, telemetry |
| `test_effort.py` | 26 | score thresholds, clamping, overrides |
| `test_cloudflare_client.py` | 19 | REST construction, account validation, strict scores, credentials and fail-open errors |
| `test_custom_client.py` | 24 | custom System One and Chat Completions transports, validation and failures |
| `test_dispatcher_integration.py` | 7 | through Hermes' own plugin manager + middleware, including OpenAI Decisions |
| `test_plugin_registration.py` | 4 | manifest, `register()` contract |
| `test_config_schema.py` | 7 | `config_schema` keys/types/defaults and scorer/model selection match middleware |
| `test_plugin_api.py` | 23 | dashboard backend (status/mode/probe/changes, no prompt leak) + desktop contract and focus-helper behavior |
| `test_desktop_decision_events.py` | 5 | public decision event payload, privacy allowlist, session boundaries and fail-open publication |
| `test_desktop_decision_event_contract.py` | 4 | Desktop event subscription, source scoping, revision handling and session-scoped selector RPC contract |
| `desktop_decision_event_behavior.test.mjs` | 8 | Executes the Desktop event/state/sync functions with Node mocks: REST precedence, ordering, profiles, focus, RPC confirmation and clear boundaries |
| `test_openai_decision_client.py` | 30 | Decisions request, key lookup, bounded prompt/guidance, strict answer validation and fail-open transport |
| `test_openrouter_client.py` | 15 | bounded OpenRouter request, strict scores, credentials, and fail-open errors |
| `test_scorers.py` | 11 | Jev model selection and explicit providers without cross-provider fallback |
| `test_rubric.py` | 21 | shared rubric, Decisions score question/parser, fixed score contract and bounded guidance |

## Adding target-model profiles

Edit `model_profiles.json`; there is no runtime documentation fetch or model-name pattern
matching. Add a new exact ID to an existing profile only when the documented effort levels,
default, and behavior summary all apply. Otherwise add a profile row with a unique `id`,
`vendor`, exact `model_ids`, `effort_levels`, nullable `default_effort`, a concise `summary`
(at most 360 characters), an HTTPS `source`, and its `reviewed` date (`YYYY-MM-DD`). IDs are
case-sensitive. Use a null default when the lab docs do not specify one. An empty
`effort_levels` list means the source does not define discrete levels; use the summary to say
whether it documents a thinking toggle, no effort control, or a non-generative task. This is
not evidence that a provider route lacks support. Keep rows separate if models differ in
supported levels, defaults, or what the control means: for example, xAI Grok 4.20 multi-agent
effort selects agent count, while standard Grok effort controls reasoning depth. Include the
OpenRouter `:free` variants as exact IDs when maintaining that dated snapshot. Keep the
OpenCode Go roster synchronized with its current docs; for provider-specific IDs without an
identified upstream lab, cite the Go listing and state that attribution is unknown instead of
inventing vendor documentation. Profiles affect scorer context only; they cannot enable route
injection or change the effort vocabulary.
`test_model_profiles.py` verifies the catalog and demonstrates that a new exact model can be
added by data-only edit.

## Running the tests

```bash
./scripts/run_tests.sh          # the invocation that works, with the interpreter that works
./scripts/run_lint.sh           # ruff, configured in ruff.toml
```

The focused Desktop behavior harness uses only Node's built-in test runner and extracts the
event/state/sync implementation from `desktop/plugin.js`:

```powershell
node --test tests/desktop_decision_event_behavior.test.mjs
```

`scripts/run_tests.sh` runs `.venv/bin/python -m pytest tests`, i.e. **the project venv**,
which is the interpreter where the plugin's tests and the Hermes core are both importable
(`hermes_cli` from `/usr/local/lib/hermes-agent`, added to `sys.path` by
`tests/conftest.py`; override with `HERMES_SOURCE_ROOT`). On a fresh machine:

```bash
./scripts/bootstrap_test_env.sh   # creates .venv --system-site-packages, installs
                                  # requirements-dev.txt, then runs the suite
```

The whole suite currently collects **503 pytest tests** and is **network-free by contract**: `tests/conftest.py` patches
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
6. `off` records bounded route metadata without rewriting or scoring.

It runs in `./scripts/run_tests.sh`; it is never skipped there.
