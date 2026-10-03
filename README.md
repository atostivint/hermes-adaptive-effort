# jev-auto-effort

A Hermes plugin that lets an external rubric scorer (Jev) pick the **reasoning effort**
of a request instead of leaving it to a fixed default.

`jev-auto-effort/` is the payload: Hermes installs it as `~/.hermes/plugins/jev-auto-effort`. This
repository is the source of that payload plus its test suite, and nothing is
pip-installable — there is no `[project]` table on purpose.

**State of this tree.** `master` is at `870aed8`. The card delivery
(`t_cb5d47d0`) and its evidence live in `docs/handoff-t_cb5d47d0.md`; the external review and
its follow-up in `docs/review-*.md`. **`docs/HANDOFF.md` is the entry point for an agent
picking this up** — verified state, the live-install provenance gap, the invariants, and
the open items.
Verified on this machine:

| check | command | result |
| --- | --- | --- |
| suite | `./scripts/run_tests.sh` | `147 passed` in ~1 s (Linux) |
| suite | `.\scripts\run_tests.ps1` | `157 passed` in ~1 s (Windows, incl. the dispatcher integration test) |
| lint | `./scripts/run_lint.sh` / `.\scripts\run_lint.ps1` | `All checks passed!` (ruff 0.16.9) |

### Windows / PowerShell

Create the local test environment and run the suite:

```powershell
.\scripts\bootstrap_test_env.ps1
```

After setup, run the suite and lint independently:

```powershell
.\scripts\run_tests.ps1
.\scripts\run_lint.ps1
```

`run_tests.ps1` uses `HERMES_SOURCE_ROOT` when set, and otherwise discovers the Hermes source
tree the way `tests/conftest.py` does: `$env:HERMES_HOME\hermes-agent`, a `hermes-agent`
checkout next to this repo, then `$env:LOCALAPPDATA\hermes`. Without that tree
`agent.reasoning_effort` is missing, effort mapping refuses by design, and ~40 mapping
tests fail for an unrelated-looking reason — the script warns when it finds nothing.
It also falls back to a scratch `--basetemp` (and disables the pytest cache) when
`%TEMP%\pytest-of-<user>` or `.pytest_cache` was left behind with a foreign ACL, which
otherwise errors every `tmp_path` test on Windows.

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

When `auto` or `cache_safe` actually changes the outgoing effort value, Jev-Auto Effort
writes a prompt-free INFO notice such as `Effort changed: medium -> high`.
In the interactive CLI, each distinct change also prints one short notice, and the status bar
keeps the last applied level (`Effort: high`). `/jev-auto-effort status` reports the last
`from -> to` change. The Desktop chip shows the
mode and latest chosen level; its pane shows the latest transition and Desktop raises a toast
for each new applied transition. To watch INFO notices in another terminal, run `hermes logs -f`.

## Commands

`/jev-auto-effort` is registered through the host's `register_command` API. The usage text is
the contract:

```text
Usage:
  /jev-auto-effort status            Show mode, settings, credential, last applied level, counts
  /jev-auto-effort status json       Machine-readable status payload
  /jev-auto-effort off|recommend|auto|cache_safe
                              Set the mode used by future requests
  /jev-auto-effort probe <text>      Classify <text> once (prints score/label, stores nothing)
  /jev-auto-effort help              Show this help
```

A mode set this way applies to **future requests served by that process**. It is not
written to `config.yaml` (the command edits no file of yours) and therefore does not
survive a restart: persist it as
`plugins.entries.jev-auto-effort.settings.mode`.

`/jev-auto-effort setup` used to appear in the usage text. It was removed rather than
implemented: the host API for plugins (`hermes_cli/plugins.py`) exposes
`register_command` and no generic setup entry point, so there was nothing to delegate to.
An unknown verb, or a mode with a stray argument, returns the usage text and changes
nothing.

`status` and `probe` classify nothing, rewrite nothing and store nothing beyond what the
middleware already holds. `probe` scores the text the operator typed — it never reads or
stores the session's conversation.

`status` derives cache safety from the **last observed request's `api_mode`**, including
unsupported requests. That route is included in each public session entry as `api_mode`
(an additive field in `jev-auto-effort.status.v1`). Before any request is observed,
the route is unknown. The verdict controls session pinning only in `cache_safe` mode;
`auto` classifies each user turn regardless of that verdict.

## Configuration

Settings live under `plugins.entries.jev-auto-effort.settings` in `config.yaml`; the defaults are
in `middleware.DEFAULTS`. Every key is also declared in `plugin.yaml`
`config_schema`, so the Desktop app renders it as a field in
**Capabilities → Plugins** (gear row) — same writer as `ctx.set_config()`, same
values the middleware reads back. No Desktop code needed for that half.

| setting | default | meaning |
| --- | --- | --- |
| `mode` | `off` | mode a fresh process starts in: `off` / `recommend` / `auto` / `cache_safe` |
| `subagent_mode` | `off` | mode for child sessions, independent of the parent |
| `endpoint` | `https://api.typesafe.ai/v1/systemone` | where a classification is POSTed |
| `timeout_s` | `3.0` | per-classification HTTP timeout; a timeout fails open |
| `max_turns` | `64` | decision-cache entries kept per process |
| `prompt_chars` | `4000` | prompt text sent to the scorer, truncated there and never stored |

The scorer's model is the `jev_client.JEV_MODEL` constant, not a setting.

`endpoint` accepts **either** the scoring route or the API base that contains it:
`https://api.typesafe.ai/v1` is normalized to `…/v1/systemone`, a bare host has
`/v1/systemone` appended, and a URL that already carries a non-version path is used
verbatim, so a proxy with its own route keeps working.

That tolerance is not cosmetic. The API root answers **404**, and a 404 here fails open on
*every* request — the plugin stays enabled, reports a present credential, and classifies
nothing. An endpoint one level too high is therefore a silent total outage, which is why
`status` prints the URL requests really go to and names the raw setting beside it whenever
the two differ.

## CLI and Desktop: controls and live status

Persistent settings and runtime status surfaces, all fail-open:

1. **Settings form (no extra install).** `config_schema` gives a persisted mode
   dropdown (`off` / `recommend` / `auto` / `cache_safe`) in
   Capabilities → Plugins. This is the persistent counterpart to
   `/jev-auto-effort <mode>`, which stays runtime-only by design (the plugin
   never writes your config from a chat command).
2. **Live toggle (unified desktop plugin, opt-in).** `desktop/plugin.js`
   contributes a status-bar chip (`Effort: <focused conversation effort>`), a `Jev Effort` pane and
   `Jev Effort: …` palette commands, backed by `dashboard/plugin_api.py`
   (`GET /status`, `GET /changes`, `POST /mode`, `POST /probe` under
   `/api/plugins/jev-auto-effort/`). Switching persists
   `settings.mode` and applies to future requests of the running process.
   The status entry allowlist includes `conversation_id`, the request's exact
   runtime session id, so the chip follows the currently focused chat and shows
   the latest decided effort for that chat. Hermes emits `on_session_end` after
   each completed turn, so those bounded per-turn entries remain available until
   the actual `on_session_finalize` / `on_session_reset` boundary clears them.
   The pane's latest transition and
   change toasts are labeled as applying across conversations.
   This requires the Desktop SDK's `focusedSessionId` and `focusedSessionOwner`
   atoms and the updated agent status schema. If the focused conversation belongs
   to another backend/profile than the current plugin REST route, the chip shows
   `N/A` until that route matches; query caches are scoped by owner and active backend.
   `GET /changes` is the bounded feed of rewrites that actually reached a request
   (`jev-auto-effort.changes.v1`: `{stream_id, events: [{id, from, to, at}], latest}`),
   polled by the chip for notifications on each applied change. It carries effort
   values only — never prompt text — and a new `stream_id`
   after a plugin reload tells a consumer to drop its cursor.
   The desktop half ships `defaultEnabled: false` and degrades to `Effort: N/A`
   when the agent half is not in `plugins.enabled` (the Python backend only
   mounts for enabled plugins — a security boundary, not a bug).
3. **Interactive CLI status bar.** The Jev plugin registers a generic host status item and
   updates it to `Effort: <level>` and prints one notice after each distinct applied change. This requires the
   Hermes `register_cli_status_item()` API; older hosts continue to provide the INFO notice
   and the last transition in `/jev-auto-effort status`.

The backend reuses `command._status_payload()` (`jev-auto-effort.status.v1`) and
`middleware.set_mode_override()` from the already-loaded agent modules, so the
chip reports the same mode and decisions `/jev-auto-effort status` prints. The
new `jev-auto-effort.changes.v1` feed holds at most 64 applied transitions in
memory, with a process stream id and monotonically increasing event ids; it has
no prompt or session identifiers. The separate status allowlist carries the
exact runtime `conversation_id` beside each decision key for focused-chat lookup.
Desktop polls every 2 seconds and establishes
an initial cursor without replaying old toasts. The core CLI host API supplies
the persistent `Effort: <level>` status item. No prompt text is stored or echoed
on any route — `probe` returns score/label/failure plus a `text_chars` count only.

## Decision cache: one decision per turn, valid only for its route

The memo key is `(session_id, turn_id)`:

* Each new turn classifies the **latest user text** in `messages` or Codex `input`,
  skipping assistant and tool results. Earlier conversation history is not the new task.
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
would add the Ox Alpha slug to `wire_efforts()`/`wire_overrides()` in `jev-auto-effort/effort.py`,
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

`unsupported` with `probes=0` can mean the Hermes provider profile emitted no effort
field, even if the model generates reasoning. Verified on the current OpenCode Go
profile: `space-bunny-free` emits no writable field; Kimi K2 and DeepSeek can emit
top-level `reasoning_effort` when reasoning is configured. Model reasoning support
alone does not establish a working effort control on a particular provider route.
Classification each turn also does not imply a different value each turn: a matching
value produces no rewrite, and narrow vocabularies can map every rubric label to the
same effort (GLM-5.2 maps `low`/`medium`/`high` to `high`).

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
jev-auto-effort/                 the payload installed as ~/.hermes/plugins/jev-auto-effort
  plugin.yaml             manifest: id, commands, hooks, settings defaults + config_schema (Desktop form)
  __init__.py             register(): commands + the llm_request middleware
  middleware.py           settings, decision cache, request rewrite, session state + effort-change feed
  effort.py               pure score -> label -> wire-effort mapping (no I/O)
  jev_client.py           HTTP client for the scorer + credential probe (lazy core import)
  cache_safety.py         is an effort change cache-neutral on this route?
  command.py              /jev-auto-effort: help, status, status json, probe, mode verbs
  dashboard/manifest.json + plugin_api.py
desktop backend: GET /status, GET /changes, POST /mode, POST /probe
  desktop/plugin.js       desktop half: effort chip, pane, change toasts, palette (opt-in)
tests/                    168 tests, one module per contract
scripts/                  run_tests.sh, run_lint.sh, bootstrap_test_env.sh (+ .ps1 for Windows)
pyproject.toml            pytest + ruff configuration
requirements-dev.txt      test/lint pins (pytest 9.1.1, ruamel.yaml 0.19.1, ruff 0.16.9)
docs/                     review reports and the handoff for card t_cb5d47d0
```

Test modules, by contract:

| module | tests | contract |
| --- | --- | --- |
| `test_middleware.py` | 31 | settings, gating, the rewrite itself, the applied-change feed |
| `test_subagent.py` | 17 | child routing, child goals, inheritance |
| `test_jev_client.py` | 18 | transport, credential probe, failure modes, endpoint normalization |
| `test_command.py` | 21 | `/jev-auto-effort` rendering, schemas, conversation identity, observed-route cache safety, no prompt leak |
| `test_cache_safety.py` | 11 | cache-neutral vs cache-hostile routes |
| `test_command_modes.py` | 10 | the `off`/`recommend`/`auto`/`cache_safe` verbs |
| `test_turn_scope.py` | 14 | the `(session_id, turn_id)` decision key, current prompt selection with full history |
| `test_review_fixes.py` | 8 | regressions found by the 2026-09-29 review |
| `test_decision_cache.py` | 7 | route-tagged decisions, re-clamp, telemetry |
| `test_effort.py` | 6 | score thresholds, clamping, overrides |
| `test_dispatcher_integration.py` | 6 | through Hermes' own plugin manager + middleware |
| `test_plugin_registration.py` | 4 | manifest, `register()` contract |
| `test_config_schema.py` | 4 | `config_schema` keys/types/defaults match `DEFAULTS` + `VALID_MODES` |
| `test_plugin_api.py` | 11 | dashboard backend (status/mode/probe/changes, no prompt leak) + desktop static contract |

## Running the tests

```bash
cd /root/workspace/Hermes/jev-auto-effort

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
boots a throwaway `HERMES_HOME`, copies the payload into `<home>/plugins/jev-auto-effort`, lets
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
* **Deployment** — the live runtime's copy, `~/.hermes/plugins/jev-auto-effort`, is a managed
  git install (`atostivint/jev-auto-effort#jev-auto-effort`) and matches this tree, GUI layer
  included. `hermes plugins update` has a source to pull from; `plugins list` still reports
  `Source: user` because a `#subdir` install publishes no `.git`. Provenance and rollback:
  `docs/HANDOFF.md` §3b and §4.
* **Activation is the operator's call** — the live `/root/.hermes/config.yaml` enables
  this plugin (`plugins.enabled`) with `settings.mode: auto`. This task deliberately
  changed nothing there: a router that rewrites billable effort is enabled by the operator,
  not by a task.
* **Ox Alpha** — see the residual risk above.
