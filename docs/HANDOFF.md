# Handoff — `hermes-adaptive-effort`

**Read this first, then `README.md`** (contract + user-facing behaviour) and the three
documents listed at the bottom (provenance and review history).

This file is the single entry point for an agent picking the work up cold. It states
what the project is, what is verified today, what is *not* finished, and the one
operational gap that is easy to miss.

---

## 1. What this is

A Hermes plugin that lets a selected external rubric scorer choose the
**reasoning effort** of each LLM request. Jev (TypeSafe) remains the default;
OpenRouter is explicit opt-in and requires a configured model.

Pipeline: read the outgoing request → score the prompt (`0 = low`, `1 = medium`,
`2 = high`) → clamp the label onto **the route's own wire vocabulary** → write it into
the effort field **that already exists** in the request.

It is fail-open by contract: any error, timeout, missing credential or unusable request
shape leaves the request **byte-for-byte untouched**.

`hermes-adaptive-effort/` is the **payload**; Hermes installs it at
`~/.hermes/plugins/hermes-adaptive-effort`. This repo is the payload's source plus its tests.
There is deliberately **no `[project]` table** — nothing here is pip-installable, so
`pip install` is never the install path. The managed path is:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort'
```

(the `#subdir` fragment points at the renamed payload directory inside the repository).

## 2. Verified state (2026-10-03)

The payload directory, plugin ID, slash command, dashboard route and Desktop identity are
`hermes-adaptive-effort`. Jev remains the default scorer. OpenRouter is explicit opt-in,
requires a configured model and `OPENROUTER_API_KEY`, and has no silent fallback to Jev.
No live OpenRouter request was made.

The Windows test suite passed **193 tests**, and Ruff passed. The current master commit
contains the implementation merge and the GitHub repository is now
`atostivint/hermes-adaptive-effort`.

### Current host installs

**Windows client.** `%LOCALAPPDATA%\hermes\plugins\hermes-adaptive-effort` points to
`C:\Users\elekt\Projets\jev-auto-effort\hermes-adaptive-effort`. The Desktop plugin link
under `%LOCALAPPDATA%\hermes\desktop-plugins\hermes-adaptive-effort` points to the payload's
`desktop/` folder. `%USERPROFILE%\.hermes\desktop-plugins\hermes-adaptive-effort\plugin.js`
is the matching standalone copy. The enabled ID and config entry were migrated to
`hermes-adaptive-effort`; the previous entry had no mode setting, so the new install keeps
the default `off`. Plugin Doctor passed with five hooks registered. The old profile Desktop
copy is preserved under `%LOCALAPPDATA%\Temp\hermes-adaptive-effort-migration-20261003-110504`.

**Iris.** `/root/.hermes/plugins/hermes-adaptive-effort` and
`/root/.hermes/desktop-plugins/hermes-adaptive-effort/plugin.js` contain the new payload and
Desktop file. Both Desktop files have SHA-256
`95c414e0e6c14c33319f8d8dccfe404e084739e36e06936b9a213dfbd249cade`. The root plugin ID is
enabled, its `mode: auto` and full TypeSafe endpoint were preserved, and install metadata
records revision `1a436a0d344c27a2ebc6b3a161fa5968fc31bd66` from
`atostivint/hermes-adaptive-effort#hermes-adaptive-effort`. The old payload, Desktop folder,
config and metadata are backed up in
`/root/.hermes/cache/scratch/hermes-adaptive-effort-migration-20261003T1200Z`.
The Hermes gateway was restarted after migration and is active with the new config.

The Windows Plugin Doctor check passed. The Iris Doctor invocation also triggered Hermes'
source/dependency updater and exited with a `state.db is corrupted after update` warning.
No database repair or restore was attempted. The file has a valid SQLite header and a
read-only `SELECT 1` succeeds; a full read-only quick-check did not complete, so database
integrity remains unverified. The gateway remained active after its restart. No live scorer
request was issued during deployment.

### Git and repository identity

`codex/windows-desktop-dev-loop` was merged into `master` as `d1d31c2` and pushed to
`https://github.com/atostivint/hermes-adaptive-effort.git`. The code payload directory is
renamed. This environment's checkout path itself remains
`C:\Users\elekt\Projets\jev-auto-effort`; keep that configured workspace path intact when
using this session.

The repo was previously `atostivint/jev-auto-effort`; Iris metadata and this handoff use
the new repository slug. For future installs, use:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort'
```

## 3. Live configuration

Windows uses the default `mode: off`. Iris remains `mode: auto` with
`endpoint: https://api.typesafe.ai/v1/systemone`; do not change either setting without
Elektro's direction. OpenRouter is selected only by explicitly setting
`scorer_provider: openrouter` and `scorer_model`; this deployment did not do that.

The endpoint is the full scoring route, retained from the prior install. Do not simplify it
to `https://api.typesafe.ai/v1`; `jev_client.normalize_endpoint()` handles base URLs, but
this exact configured endpoint was previously verified live.

## 4. Deployment and recovery notes

The plugin directory and Desktop plugin are separate install surfaces. Deploy both when
`desktop/plugin.js` changes, then restart the relevant Hermes process so already-imported
Python modules reload. On Iris, the plugin/config migration backup above can restore the old
payload and configuration if rollback is needed. On Windows, the profile Desktop backup is
under the recorded `%LOCALAPPDATA%\Temp` path; the old source junctions were replaced only
after their targets were verified.

The Iris gateway is `hermes-gateway.service` in root's user systemd manager. Existing
isolated Desktop serve processes are separate long-lived processes and may need to reconnect
or restart before they import changed Python modules.

## 5. Open items

- No real OpenRouter scoring request or cost/latency comparison has been run.
- A full SQLite integrity check for Iris's default `state.db` remains unverified after the
  Hermes updater warning; do not repair or replace that database as part of plugin work.
- The checkout directory on this Windows machine still has the historical `jev-auto-effort`
  folder name because the active workspace is rooted there; the GitHub slug and payload code
  identity are already renamed.
## 6. Architecture invariants — do not break these

1. **Default mode is `off`.** `off` / `recommend` (classify, rewrite nothing) / `auto`
   (rewrite an existing field) / `cache_safe` (per-turn where cache-neutral, else
   session-pinned).
2. **Rewrite only a field that already exists.** `middleware._effort_slot` recognises
   `extra_body.reasoning.effort`, top-level `reasoning_effort`, and top-level
   `reasoning.effort` (codex_responses). Never invent a field; never re-enable thinking;
   never touch `"none"` or `enabled: false`. No writable field ⇒ `unsupported`, **zero**
   scorer calls.
3. **Clamp onto the route vocabulary.** `effort.map_effort` → `clamp_effort` plus narrow
   tables for Kimi K3 / GLM-5.2 / GLM-5.3; `openai-codex` skips the narrow table;
   unknown routes fall back to the widest OpenAI-compatible set.
4. **One selected-scorer call per turn.** Memo key `(session_id, turn_id)`; `failed`/`unsupported` are
   never retried in-turn; concurrent probes are claimed via `_IN_FLIGHT`; a stored target
   is re-clamped when the route changes (`_target_for_route`).
5. **Rubric:** score `0..2` → `low` (<0.5) / `medium` (<1.5) / `high`. Out of range,
   NaN/inf, bool, non-numeric → `None` → fail open.
6. **Fail-open everywhere.** `on_llm_request` catches all exceptions. Stable reason codes:
   `invalid_prompt`, `credential_missing`, `http_error`, `timeout`, `transport_error`,
   `malformed_response`, `unexpected_error`, `classifier_error`.
7. **Subagents** are classified from the parent-written goal (`subagent_start`), gated by
   the independent `subagent_mode`. `probe`/`status` classify and store nothing; `probe`
   only ever scores operator-typed text.
8. **No prompt storage, no prompt in logs/reasons/traces.** Effort-change notices log
   only the old and new effort values. Reason strings carry effort values only;
   `command._ENTRY_FIELDS` is the only rendered session allowlist.
9. **Never write the operator's config from a chat command.** `/hermes-adaptive-effort <mode>`
   sets an in-process `_MODE_OVERRIDE` only; the persist path is
   `plugins.entries.hermes-adaptive-effort.settings.mode`.
10. **Schemas are a public contract:** `hermes-adaptive-effort.status.v1`,
    `hermes-adaptive-effort.probe.v1`, and `hermes-adaptive-effort.changes.v1`. Field names and reason
    codes are not free to rename.
11. **Cache safety:** `cache_safety.effort_is_cache_safe()` returns `True` only for
    `chat_completions` / `codex_responses`; `anthropic_messages` and anything unknown →
    `False` (pin the session, never gamble the cache).

## 7. Desktop and CLI effort visibility

The plugin, dashboard backend, Desktop surface and Hermes CLI host API are separate layers:

* **`plugin.yaml` → `config_schema`** — renders a settings form in Desktop
  Capabilities → Plugins. Saving writes `plugins.entries.hermes-adaptive-effort.settings.<key>`,
  which `middleware._read_setting` reads back per call (not frozen at import). Types and
  `choices` **must** stay in sync with `middleware.DEFAULTS` / `VALID_MODES`; the
  settings writer refuses mismatches, and `tests/test_config_schema.py` enforces it.
* **`dashboard/plugin_api.py`** — FastAPI backend mounted at
`/api/plugins/hermes-adaptive-effort/`: `GET /status`, `GET /changes`, `POST /mode`,
  `POST /probe`. It reuses
  the already-loaded agent modules (`command._status_payload()`,
  `middleware.set_mode_override()`), so the chip reports exactly what
  `/hermes-adaptive-effort status` prints. `probe` returns score/label/failure and a
  `text_chars` count only — **never the text**. It degrades to an `error: status_failed`
  payload rather than raising. Each status entry carries an allowlisted
  `conversation_id` alongside its decision-key `session_id`; this is the exact
  runtime session id used to scope the Desktop chip. `changes.v1` retains at most 64
  prompt-free applied transitions in memory and does not contain session identifiers.
* **`GET /changes`** — the feed of rewrites that actually reached a request:
  `{stream_id, events: [{id, from, to, at}], latest}` under
  `hermes-adaptive-effort.changes.v1`, effort values only, never prompt text.
  `middleware._record_effort_change()` is called at the single point where a rewritten
  request is returned, so `recommend` / `failed` / `unsupported` / no-op turns and a
  tool loop re-sending the applied value record nothing; it also deduplicates on
  `(decision_key, from, to)`, so a route change re-sending the original level does not
  replay an event. Ids are monotonic within one `stream_id`; `reset_state()` mints a new
  `stream_id`, which is the chip's signal to drop its cursor (it must — the sequence
  restarts). Degrades to a `503 agent_plugin_not_loaded` / `changes_failed` payload
  like `/status`.
* **`desktop/plugin.js`** — opt-in desktop plugin (`defaultEnabled: false`): a status-bar
  chip `Effort: <focused conversation's latest decided effort>`, a `Adaptive Effort` pane with
  the global latest transition, and one toast per newly observed applied change. Toasts
  identify the change as belonging to a conversation because the feed has no session id.
  The changes feed and chip status poll every 2 s;
  the chip reads `host.state.focusedSessionId` and matches `conversation_id` exactly, so
  another chat's activity cannot change its effort. A focused chat with no decision,
  an unsupported request, or an in-flight classification shows `Effort: N/A`; the
  global changes feed never supplies a fallback effort. Initial history is treated as
  a baseline, not replayed as toasts. Pane status/mode polling remains every 15 s.
  Shows `Effort: N/A` when the backend is absent.
* **Interactive CLI status bar** — the plugin registers `Effort: —` through the generic
  Hermes `PluginContext.register_cli_status_item()` host API and updates it after each
  distinct applied rewrite. The status handle also prints one bounded notice above the
  prompt for each distinct applied change; the plugin logs the same prompt-free `from -> to` notice and
  `/hermes-adaptive-effort status` prints the last applied transition. Older Hermes hosts without
  the status-item API still get the log notice and command output.

**Security boundary, not a bug:** the Python backend only mounts for plugins listed in
`plugins.enabled`. If the backend is genuinely absent or unreachable (for example the
plugin is disabled, or the dashboard half is not deployed), the chip correctly shows
`Effort: —` rather than failing loudly.

## 8. Open items, in the order I would take them

1. **Ox Alpha / `x-preview-f-free`:** `medium` returns HTTP 400. This is a **known gap,
   deliberately not worked around** — silently remapping it would hide a real vendor
   rejection. Either document it further or make it fail open loudly.
2. **Cost effect is unmeasured.** No live A/B has been run, so every cost or cache claim
   in the README is an expectation, not a measurement. Do not restate them as results.
3. **Pre-install backups** sit in `~/.hermes/cache/scratch/` (`jev-backup-20260930-080501`,
   `jev-backup-20260930-082258`). Harmless, and they are the rollback path if the managed
   install ever needs undoing. Clear them once you are satisfied (§3b).

## 9. Working on this repo

```bash
./scripts/run_tests.sh          # always this, never bare `pytest` with a different python
./scripts/run_lint.sh           # ruff 0.16.9
./scripts/bootstrap_test_env.sh # fresh machine only
```

* Always use `.venv/bin/python`: it has the Hermes source tree on `sys.path` via
  `tests/conftest.py`. Never `pip install` the payload.
* `pyproject.toml`: `target-version = "py310"`, `line-length = 100`,
  `select = ["E","F","W","B"]`, `ignore = ["E501"]`. Import sorting (`I`) is
  intentionally **off** — do not reorder imports.
* Tests import the payload as `hermes_plugin_adaptive_effort.<stem>`; inject fakes through
  `_classifier_factory` or the client's `transport=` / `key_reader=`, never real HTTP.
* `hermetic_plugin_settings` is autouse: `_config_reader` returns `{}`,
  `_classifier_factory` is `None`, `reset_state()` runs before and after. No unit test may
  read `~/.hermes/config.yaml`.
* Keep `effort.py` pure (stdlib only, lazy `agent.*` imports inside functions).
* Behaviour change ⇒ update the `README.md` contract tables **and** this file.
* Never read or print `.env` / credentials. `credential_present()` returns a bool and
  must stay that way.

## 10. Prior documents (provenance and review history)

| file | what it holds |
| --- | --- |
| `docs/handoff-t_cb5d47d0.md` | the original card handoff (FR): provenance, the 10 acceptance criteria, route/vocabulary matrix, deploy + rollback, open risks. Predates the rename and the GUI. |
| `docs/review-deepseek-2026-09-29.md` | a severe external review (FR): host-contract checks, bugs by severity, security, cache-risk analysis, activation verdict. |
| `docs/review-followup-2026-09-29.md` | which review findings were fixed, and the install state as of 2026-09-29. |

`AGENTS.md` in the repo root is the agent-facing contract (layout, invariants, test
conventions) — read it before touching anything.
