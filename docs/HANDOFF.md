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
hermes plugins install 'atostivint/jev-auto-effort#hermes-adaptive-effort'
```

(the `#subdir` fragment points at the new payload dir inside the still-legacy-named
GitHub repository; that repository rename is tracked separately below).

## 2. Verified state (re-checked on this machine, 2026-09-30)

Provider generalization and code identity, 2026-10-03: the payload, plugin ID, command,
dashboard route and desktop identity now use `hermes-adaptive-effort`. The Jev adapter
remains the default; an OpenRouter adapter uses `OPENROUTER_API_KEY`, a configured
`scorer_model`, strict JSON numeric scoring, and a bounded 32-token completion. Provider
selection is explicit with no cross-provider fallback. No live OpenRouter request was
sent. The Windows suite is **193 passed** and lint is clean. The Sol plan recommended an
explicit adapter boundary and a staged identity migration that preserves old settings
while never enabling both middleware IDs together.

Update, 2026-10-03: reproduced and fixed a per-turn bug where new turn ids still
classified the oldest user message from full conversation history. Normal turns now
select the latest user text in Chat Completions `messages` or preflighted Codex `input`;
subagents still classify the parent-written goal. Six new regression cases cover all
three effort shapes in `auto` and cache-safe routing, including tool-loop reuse and
low → high → low decisions. Four status cases verify the observed `api_mode` is stored
and allowlisted, and the cache-safety line describes the latest recorded route rather
than always claiming an unknown route. `.\scripts\run_tests.ps1`: **167 passed**;
`.\scripts\run_lint.ps1`: **All checks passed**.

Lifecycle follow-up, 2026-10-03: live Iris runs showed Hermes calls
`on_session_end` after each completed turn with a `turn_id`. The plugin now keeps
the bounded effort entries for the focused-conversation chip and clears them at
`on_session_finalize` / `on_session_reset`. Commit `7412e4b` passed the Windows
suite (**168 passed**) and lint. It is deployed on Iris: plugin validation and
doctor pass, and the running gateway hot-reload reports all five expected hooks.
The local plugin junction resolves to the same checkout, and local Plugin Doctor
also reports all five hooks. Iris had no registered live Desktop serve backend
for activation at deploy time; a long-lived isolated serve process must reconnect
or restart to reload Python modules already held in memory.

A network-free check using the actual CLI source tree selected by `hermes.cmd`
(`hermes-agent-project-picker`), its OpenCode Go profile and ChatCompletionsTransport
confirmed: `space-bunny-free` emits no effort field (unsupported, zero probes);
Kimi K2.5 and DeepSeek V4 Pro emit `reasoning_effort` and follow low → high → low
with one fake-scoring call per turn; GLM-5.2 is classified each turn but all rubric
labels clamp to `high`. No vendor requests, cost measurements or cache A/B were run.
The installed plugin's middleware matched the source baseline before this fix;
source test success alone does not prove a running Hermes process has loaded the fix.
Verified installation detail: `%LOCALAPPDATA%\hermes\plugins\hermes-adaptive-effort` is a
Windows junction targeting this repository's `hermes-adaptive-effort/` payload. Both changed
installed files have the same hashes as the fixed source, so no copy is needed.
Restart the Hermes process to reload Python modules; editing the linked files does
not replace modules already held in memory.

Conversation-aware chip update, 2026-10-03: **168 tests passed**, lint clean.
An independent Node rendering check covered switching A/B, unsupported B,
concurrent activity in C, an unknown conversation, and a different backend owner.
The local `%USERPROFILE%\.hermes\desktop-plugins\hermes-adaptive-effort\plugin.js` copy
was backed up and updated, with its hash verified against the tested source.
The chip requires the current Desktop SDK focus atoms and the updated Python
status entries; deploy both halves together. A focused owner that differs from
the active plugin REST backend/profile renders `N/A` rather than another route's
cached effort. Iris was inspected read-only and still has the older middleware;
the fixes have not been deployed there.

| check | command | result |
| --- | --- | --- |
| suite | `./scripts/run_tests.sh` | **`147 passed`** in 1.15s (Linux) |
| suite | `.\scripts\run_tests.ps1` | **`157 passed`** in 0.84s (Windows, dispatcher integration included) |
| lint | `./scripts/run_lint.sh` / `.\scripts\run_lint.ps1` | `All checks passed!` (ruff 0.16.9) |
| network | — | the suite is network-free by fixture (`no_network` autouse, session-scoped) |

Test count by contract (one module per contract, per `AGENTS.md`):

```text
test_middleware.py            31   test_jev_client.py        18
test_command.py               16   test_subagent.py          17
test_cache_safety.py          11   test_plugin_api.py        11
test_turn_scope.py             8   test_review_fixes.py       8
test_effort.py                 6   test_dispatcher_integration.py  6
test_config_schema.py          4   test_plugin_registration.py     4
test_command_modes.py         10   test_decision_cache.py     7
```

`test_dispatcher_integration.py` is the one that boots a throwaway `HERMES_HOME` with a
real `PluginManager.discover_and_load()` + `apply_llm_request_middleware`. It is not
skipped. Keep it that way — it is the only test that proves the plugin loads through
real discovery rather than through the test harness.

## 3. Git state

Work is on branch `codex/windows-desktop-dev-loop`, pushed to `origin`. It merges
`codex/jev-effort-visibility` (also on origin) so there is a single change-feed
implementation. `master` is still `870aed8` and matches `origin/master` — **nothing is
merged to master yet.** `AGENTS.md` is tracked (committed at Elektro's request — it is the
agent contract, read it first).

```text
a880e82  Merge codex/jev-effort-visibility: one change feed, the stronger implementation
36e497f  docs: record the branch, the new commits, and the deploy gap they open
82b95c7  Add live CLI and Desktop effort indicators (codex/jev-effort-visibility)
00bcdb1  chore: make the suite runnable on Windows (PowerShell scripts, normcase fix)
8d76a95  feat: serve the applied-effort change feed the desktop chip polls
870aed8  Manifest/code parity + HANDOFF entry point
ff6067a  Track AGENTS.md contributor instructions
7d1f066  Add Desktop GUI: config_schema settings form + unified desktop plugin
a81d833  refactor: rename the plugin to hermes-adaptive-effort
8a73601  fix: reach the scoring route when settings.endpoint names the API base
67429e3  docs: add the handoff for card t_cb5d47d0
82d201a  wip(kanban t_cb5d47d0 run22): parked at 60/60 iterations
7ca51bd  chore: commit the working tree the live runtime executes
```

Note: an earlier revision of this file named `0949fb6` where `870aed8` is correct.
`0949fb6` is a real object but is **not** on `master` — it is the pre-amend commit
whose only delta is this very line, so it is not reachable from any branch. Trust
`git log --oneline master`, never a SHA transcribed into prose.

Stale branch `wip/kanban-t_cb5d47d0-run22` (`82d201a`) still exists locally; it predates
the endpoint fix and the rename. Nothing depends on it.

### 3a. Where each host actually runs this

Two hosts, two mechanisms, both on `a880e82` (the merge) as of 2026-10-02.

**This machine (Windows).** `~/AppData/Local/hermes/plugins/hermes-adaptive-effort` is a
**symlink to `C:\Users\elekt\Projets\hermes-adaptive-effort\hermes-adaptive-effort`** — the repo working
tree itself. There is nothing to deploy: any commit is live on the next agent start. Run
`hermes plugins doctor hermes-adaptive-effort` if you want to see it resolve. The desktop half is
the separate copy `~/.hermes/desktop-plugins/hermes-adaptive-effort/plugin.js` and does need a
re-copy when `desktop/plugin.js` changes.

**Iris.** `/root/.hermes/plugins/hermes-adaptive-effort` is a managed `#subdir` install of
`atostivint/hermes-adaptive-effort`, deployed by copying the payload over it (its
`.install-metadata.json` `revision` is maintained by hand to match). Iris's payload was
already running a `/changes` feed — the `codex/jev-effort-visibility` implementation —
because it had been copied there by hand; that branch has now been **merged** into
`codex/windows-desktop-dev-loop`, so there is one implementation, not two.

Deploying to iris means: copy `hermes-adaptive-effort/` over the payload dir, `scp`
`desktop/plugin.js` to `/root/.hermes/desktop-plugins/hermes-adaptive-effort/plugin.js`, clear
`__pycache__`, and set `.install-metadata.json` `revision` to the deployed SHA. Rollback:
`/root/.hermes/cache/scratch/jev-backup-<stamp>/` holds both trees as they were.

Note `plugins/` and `desktop-plugins/` are **gitignored** in iris's own `/root/.hermes`
repo (`Elektro121/iris-configs`, synced every few hours), so the payload is not versioned
there — only these backups are.

## 3b. ✅ The live install is a managed git install (was a hand-copy)

Done 2026-09-30. Hermes used to report this plugin as `Source: user` with **no entry** in
`~/.hermes/plugins/.install-metadata.json`, unlike every other git-installed plugin here.
That is why `hermes plugins update hermes-adaptive-effort` failed with *"not installed from git"*.

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort' --force --enable
```

The `#subdir` fragment points the installer at the `hermes-adaptive-effort/` payload directory.
It clones via the `gh`-authenticated credential helper (the repo is **private**, scope
`repo`) and records `revision: 870aed81…` plus `source` in `.install-metadata.json`.
Verified after the fact: payload byte-identical to the repo, `plugins.entries` settings
(`mode: auto`, the full `endpoint`) preserved, still enabled, `doctor` and `validate`
green, desktop half untouched, `hermes plugins update` now reports *already up to date*.

**`plugins list` still shows `Source: user` for this plugin. That is expected, not a
failed install.** The label is derived from a `.git` directory on disk
(`plugins_cmd.py:780`: `src_label = "git" if source == "user" and (d / ".git").exists()`),
and a `#subdir` install publishes a sparse checkout without one. `jev-approvals` is the
same shape (`…git#plugin`, no `.git`) and also reads `user`. Only a whole-repo install
(`file-tray`) shows `git`. The managed state lives in `.install-metadata.json`.

Note the *desktop* half is a separate, app-level copy in `~/.hermes/desktop-plugins/`
and `hermes plugins install` does **not** touch it — it was verified still byte-identical
after the conversion. That copy is marker-less (hand-copied), but identical to the
package's `desktop/plugin.js`, so when the Desktop app next runs `materializeDesktopHalf()`
for this package (an app-level install from **Capabilities → Plugins**) its adoption branch
stamps the marker and pairs the row with no change to the file. Until then the chip still
works; the marker only affects how the row is displayed and tracked.

## 4. ✅ The live install is current (this section was stale)

An earlier revision of this file carried a "deployment gap" warning here, claiming the
live install sat at `a81d833` and was missing the whole GUI layer. **That is no longer
true — re-verify before believing any version of this claim.** Re-checked 2026-09-30:

```text
~/.hermes/plugins/hermes-adaptive-effort/  ==  master (870aed8)
```

* `diff -r` (excluding `__pycache__` / `*.pyc`) reports **no differences** against
  `hermes-adaptive-effort/` in the repo.
* `plugin.yaml`, `dashboard/plugin_api.py` and `desktop/plugin.js` are all **present**.
* The `config_schema:` block **is** in the live `plugin.yaml`.
* `~/.hermes/desktop-plugins/hermes-adaptive-effort/plugin.js` is **present and byte-identical**
  to the package's `desktop/plugin.js`.

So the Desktop GUI layer from `7d1f066` *is* deployed on both tiers. Do not re-copy
anything, and do not re-run a "deploy the GUI" task: it has nothing left to do.

The one real gap on this plugin is **provenance, not bytes** — see §3b. Converting the
hand-copy to a managed git install does not change any file content; it only adds
`.install-metadata.json` so that `hermes plugins update` has a source to pull from.

## 5. Live configuration (unchanged by this work, on purpose)

`/root/.hermes/config.yaml` already enables the plugin:

```yaml
plugins:
  enabled:
    - hermes-adaptive-effort
  entries:
    hermes-adaptive-effort:
      settings:
        mode: auto
        endpoint: https://api.typesafe.ai/v1/systemone
```

`endpoint` names the **full scoring route**, which is the value `8a73601` fixed: it had
been set to the API base, so every classification POSTed to the root, got `404`, and
failed open as `http_error`. The symptom was nasty — plugin enabled, credential present,
effort never rewritten. `jev_client.normalize_endpoint()` now maps a base URL onto the
scoring route, and `status` prints `endpoint_effective` plus the raw setting whenever the
two differ. Do not "tidy" that endpoint back to `https://api.typesafe.ai/v1`.

A router that rewrites billable effort is activated by Elektro, not by a task or an
agent. Do not change `mode` in his config.

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
