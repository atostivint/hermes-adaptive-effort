# jev-auto

A Hermes plugin that lets an external rubric scorer (Jev) pick the **reasoning effort**
of a request instead of leaving it to a fixed default.

`jev-auto/` is the payload: Hermes installs it as `~/.hermes/plugins/jev-auto`. This
repository is the source of that payload plus its test suite, and nothing is
pip-installable — there is no `[project]` table on purpose.

**State of this tree.** Base commit `7ca51bd` ("chore: commit the working tree the live
runtime executes") plus the delivery for card `t_cb5d47d0`. Evidence, exact commands and
the provider/model matrix live in `docs/handoff-t_cb5d47d0.md`. Verified on this machine:

| check | command | result |
| --- | --- | --- |
| suite | `./scripts/run_tests.sh` | `127 passed` in ~8 s |
| lint | `./scripts/run_lint.sh` | `All checks passed!` (ruff 0.16.9) |

## What it does

1. Reads the request Hermes is about to send (`llm_request` middleware).
2. Asks Jev for a rubric score on the prompt: `0 = low`, `1 = medium`, `2 = high`, with
   the thresholds `<0.5`, `0.5..<1.5`, `>=1.5`.
3. Clamps that label onto the **route's own declared vocabulary**
   (`agent.reasoning_effort.clamp_effort`), so an unsupported wire value is never sent.
4. Writes the result into the reasoning-effort field that is **already present** in the
   request.

It never invents a field, never re-enables thinking on a request that disabled it, never
turns thinking off, and never touches anything but that one slot.

| mode | what happens | Jev call |
| --- | --- | --- |
| `off` (default) | nothing at all | no |
| `recommend` | classify, report the level it would use, rewrite nothing | yes |
| `auto` | classify and rewrite an existing effort field | yes |
| `cache_safe` | per turn on routes where an effort change keeps the prompt cache; otherwise one level pinned for the whole session | yes |

## Commands

`/jev-auto` is registered through the host's `register_command` API. The usage text is
the contract:

```text
Usage:
  /jev-auto status            Show mode, settings, credential, session counts
  /jev-auto status json       Machine-readable status payload
  /jev-auto off|recommend|auto|cache_safe
                              Set the mode used by future requests
  /jev-auto probe <text>      Classify <text> once (prints score/label, stores nothing)
  /jev-auto help              Show this help
```

A mode set this way applies to **future requests served by that process**. It is not
written to `config.yaml` (the command edits no file of yours) and therefore does not
survive a restart: persist it as
`plugins.entries.jev-auto.settings.mode`.

`/jev-auto setup` used to appear in the usage text. It was removed rather than
implemented: the host API for plugins (`hermes_cli/plugins.py`) exposes
`register_command` and no generic setup entry point, so there was nothing to delegate to.
An unknown verb, or a mode with a stray argument, returns the usage text and changes
nothing.

`status` and `probe` classify nothing, rewrite nothing and store nothing beyond what the
middleware already holds. `probe` scores the text the operator typed — it never reads or
stores the session's conversation.

## Decision cache: one decision per turn, valid only for its route

The memo key is `(session_id, turn_id)`:

* A multi-call turn (a tool loop) is **several requests of one turn**: it reuses the
  decision from its first call — one Jev call, not one per request. `api_call_count` is
  not consulted; the turn id is the only authority.
* The entry records the **provider and the model** the decision was made on, plus the
  label, the target, the request/probe counters and the outcome (`state`).
* On reuse, the recorded *target* is **re-clamped onto the current route**. The label
  describes the prompt and stays valid; the wire value is per route and does not. This is
  what protects a provider fallback inside one turn: a `medium` recorded on a wide route
  is never replayed verbatim onto a route that spells its middle level `high`.
* `failed` and `unsupported` outcomes are **not retried inside the turn** — a classifier
  outage costs at most one probe per turn, not one per request.

`cache_safe` narrows the key further, to the session, when an effort change would
invalidate the prompt cache (see below).

## Route vocabulary: what may be written where

The plugin uses Hermes' own data (`agent.json`) — `agent/reasoning_effort.py` — for the
route, and adds the narrow vendor sets that the host's *entry* clamp does not apply,
because an `llm_request` hook runs **after** the transport clamp.

| route (bare model slug) | vocabulary used | notes |
| --- | --- | --- |
| `kimi-k3*`, `k3`, `k3-256k`, `moonshot*` | `low`, `high`, `max` | `medium` rounds **up** to `high` (K3's positional middle and server default) |
| `glm-5.2*` | `high`, `max` | `low` and `medium` are not offered by this route |
| `glm-5.3*` | `low`, `medium`, `high`, `max` | graded scale, monotonic in reasoning tokens |
| any other non-Codex route | `route_supported_efforts(provider, model)` | for an unknown route this is the widest OpenAI-compatible set |
| `openai-codex` | `route_supported_efforts(...)` | the narrow `wire_efforts` table is skipped for this provider |

Corrected core facts (the previous version of this README quoted an older ladder):

```python
EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
OPENAI_COMPAT_WIRE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
```

`ultra` is Hermes-internal and appears in no wire set. Unset stays unset: the plugin
never introduces an effort where the request had none.

### Residual risk: Ox Alpha / `x-preview-f-free`

OpenCode "Ox Alpha" accepts exactly `low`/`high`/`max`; `medium` is a 400. The core
declares `OX_ALPHA_EFFORTS` and `OX_ALPHA_OVERRIDES` but exposes **no route selector**
for that slug, so `route_supported_efforts` hands this plugin the wide set and the plugin
can legitimately choose `medium` — which the vendor then rejects. This is a live, unfixed
risk, tracked by criterion 8 of the card and **not** worked around here (a follow-up
would add the Ox Alpha slug to `wire_efforts()`/`wire_overrides()` in `jev-auto/effort.py`,
mirroring the Kimi and GLM entries that are already there).

## Fail-open behaviour

| situation | request | request state | Jev calls |
| --- | --- | --- | --- |
| no credential | unchanged | no entry created | 0 |
| classifier timeout / transport error | unchanged | `failed`, one probe | 1 (never retried in the turn) |
| score out of `0..2`, non-finite, non-numeric | unchanged | `failed`, one probe | 1 |
| no writable effort field (e.g. reasoning disabled) | unchanged | `unsupported` | 0 |
| label has no legal level on the route | unchanged | `unsupported` | 0 |
| any internal exception in the plugin | unchanged | — | — |

The plugin never breaks a turn: a failure to route effort leaves the request exactly as
the host built it.

## Prompt cache

An effort change is visible to the cache layer only when the route renders the thinking
configuration into the prompt (Anthropic-style `anthropic_messages` routes). `cache_safe`
therefore:

* keeps the **per-turn** key on routes where an effort change is cache-neutral
  (`chat_completions`, `codex_responses`);
* drops the turn id and **pins one level for the whole session** on cache-hostile routes,
  so at most one cache invalidation happens per session.

What is **not** measured: the real effect on `cache_read_tokens` / cost on this box. That
would need an A/B run against the live provider; no such measurement was performed, and
nothing in this repository claims a number for it.

## Subagents

Child sessions are routed, not ignored: the child's goal is read through
`child_goals()`, and a mechanical goal (the child is doing a rename, not reasoning about
a design) is not upgraded. A child inherits the parent's mode but its decision is its own
entry, keyed by its own session and turn.

## Files

```text
jev-auto/                 the payload installed as ~/.hermes/plugins/jev-auto
  plugin.yaml             manifest: id, commands, hooks, settings defaults
  __init__.py             register(): commands + the llm_request middleware
  middleware.py           settings, mode, decision cache, request rewrite, session state
  effort.py               pure score -> label -> wire-effort mapping (no I/O)
  jev_client.py           HTTP client for the scorer + credential probe (lazy core import)
  cache_safety.py         is an effort change cache-neutral on this route?
  command.py              /jev-auto: help, status, status json, probe, mode verbs
tests/                    127 tests, one module per contract
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh
pyproject.toml            pytest + ruff configuration
requirements-dev.txt      test/lint pins (pytest 9.1.1, ruamel.yaml 0.19.1, ruff 0.16.9)
docs/                     review reports and the handoff for card t_cb5d47d0
```

Test modules, by contract:

| module | tests | contract |
| --- | --- | --- |
| `test_middleware.py` | 24 | settings, gating, the rewrite itself |
| `test_subagent.py` | 17 | child routing, child goals, inheritance |
| `test_jev_client.py` | 14 | transport, credential probe, failure modes |
| `test_command.py` | 13 | `/jev-auto` rendering, schemas, no prompt leak |
| `test_cache_safety.py` | 11 | cache-neutral vs cache-hostile routes |
| `test_command_modes.py` | 10 | the `off`/`recommend`/`auto`/`cache_safe` verbs |
| `test_turn_scope.py` | 8 | the `(session_id, turn_id)` decision key |
| `test_review_fixes.py` | 8 | regressions found by the 2026-09-29 review |
| `test_decision_cache.py` | 7 | route-tagged decisions, re-clamp, telemetry |
| `test_effort.py` | 6 | score thresholds, clamping, overrides |
| `test_dispatcher_integration.py` | 6 | through Hermes' own plugin manager + middleware |
| `test_plugin_registration.py` | 3 | manifest, `register()` contract |

## Running the tests

```bash
cd /root/workspace/Hermes/jev-auto-plugin

./scripts/run_tests.sh          # the invocation that works, with the interpreter that works
./scripts/run_lint.sh           # ruff, configured in pyproject.toml
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
makes plugin settings hermetic — tests never read `~/.hermes/config.yaml`, so a live
profile with `mode: auto` cannot turn a "default is off" test red.

### The integration test is real, and it runs

`tests/test_dispatcher_integration.py` does not call the plugin's callback directly: it
boots a throwaway `HERMES_HOME`, copies the payload into `<home>/plugins/jev-auto`, lets
Hermes' own `PluginManager.discover_and_load()` find and register it, and then enters
through `hermes_cli.middleware.apply_llm_request_middleware` — the function
`agent/turn_api_request.py` calls before building a provider request. It covers, in that
real path:

1. a turn is rewritten once and reported as changed;
2. a tool loop (three requests of one turn) reuses that decision: **one** Jev call;
3. a provider fallback inside the turn re-clamps the target to the new route instead of
   replaying a stale level;
4. a classifier failure fails open, costs one probe, and is not retried in the turn;
5. a request with no writable effort field is reported `unsupported` with no Jev call;
6. `off` exercises nothing (no rewrite, no Jev call).

It runs in `./scripts/run_tests.sh`; it is never skipped there.

## Not covered / open

* **Cost effect unmeasured** — see "Prompt cache" above.
* **Deployment** — the copy the live runtime executes, `~/.hermes/plugins/jev-auto`, is
  byte-identical to `7ca51bd`, i.e. it does **not** contain this delivery. Reload and
  rollback are in the handoff doc.
* **Activation is the operator's call** — the live `/root/.hermes/config.yaml` enables
  this plugin (`plugins.enabled`) with `settings.mode: auto`. This task deliberately
  changed nothing there: a router that rewrites billable effort is enabled by Elektro,
  not by a task.
* **Ox Alpha** — see the residual risk above.
