# Handoff — `jev-auto-effort`

**Read this first, then `README.md`** (contract + user-facing behaviour) and the three
documents listed at the bottom (provenance and review history).

This file is the single entry point for an agent picking the work up cold. It states
what the project is, what is verified today, what is *not* finished, and the one
operational gap that is easy to miss.

---

## 1. What this is

A Hermes plugin that lets an external rubric scorer (**Jev**, TypeSafe) choose the
**reasoning effort** of each LLM request instead of leaving it at a fixed default.

Pipeline: read the outgoing request → score the prompt (`0 = low`, `1 = medium`,
`2 = high`) → clamp the label onto **the route's own wire vocabulary** → write it into
the effort field **that already exists** in the request.

It is fail-open by contract: any error, timeout, missing credential or unusable request
shape leaves the request **byte-for-byte untouched**.

`jev-auto-effort/` is the **payload**; Hermes installs it at
`~/.hermes/plugins/jev-auto-effort`. This repo is the payload's source plus its tests.
There is deliberately **no `[project]` table** — nothing here is pip-installable, so
`pip install` is never the install path. The managed path is:

```bash
hermes plugins install 'atostivint/jev-auto-effort#jev-auto-effort'
```

(the `#subdir` fragment points at the payload dir inside this repo). The live install
now follows that path, with the exact command recorded in §3b.

## 2. Verified state (re-checked on this machine, 2026-09-30)

| check | command | result |
| --- | --- | --- |
| suite | `./scripts/run_tests.sh` | **`147 passed` in 1.15s** |
| lint | `./scripts/run_lint.sh` | `All checks passed!` (ruff 0.16.9) |
| manifest | `hermes plugins doctor jev-auto-effort` | `OK: runtime discovery, manifest parsing, import, and registration passed` — 0 tools, 3 hooks |
| network | — | the suite is network-free by fixture (`no_network` autouse, session-scoped) |

Test count by contract (one module per contract, per `AGENTS.md`):

```text
test_middleware.py            24   test_jev_client.py        18
test_command.py               16   test_subagent.py          17
test_cache_safety.py          11   test_plugin_api.py         8
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

`master` matches `origin/master`; working tree clean. `AGENTS.md` is tracked
(committed at Elektro's request — it is the agent contract, read it first).

```text
870aed8  Manifest/code parity + HANDOFF entry point
ff6067a  Track AGENTS.md contributor instructions
7d1f066  Add Desktop GUI: config_schema settings form + unified desktop plugin
a81d833  refactor: rename the plugin to jev-auto-effort
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

## 3b. ✅ The live install is a managed git install (was a hand-copy)

Done 2026-09-30. Hermes used to report this plugin as `Source: user` with **no entry** in
`~/.hermes/plugins/.install-metadata.json`, unlike every other git-installed plugin here.
That is why `hermes plugins update jev-auto-effort` failed with *"not installed from git"*.

```bash
hermes plugins install 'atostivint/jev-auto-effort#jev-auto-effort' --force --enable
```

The `#subdir` fragment points the installer at the `jev-auto-effort/` payload directory.
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
~/.hermes/plugins/jev-auto-effort/  ==  master (870aed8)
```

* `diff -r` (excluding `__pycache__` / `*.pyc`) reports **no differences** against
  `jev-auto-effort/` in the repo.
* `plugin.yaml`, `dashboard/plugin_api.py` and `desktop/plugin.js` are all **present**.
* The `config_schema:` block **is** in the live `plugin.yaml`.
* `~/.hermes/desktop-plugins/jev-auto-effort/plugin.js` is **present and byte-identical**
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
    - jev-auto-effort
  entries:
    jev-auto-effort:
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
   Jev calls.
3. **Clamp onto the route vocabulary.** `effort.map_effort` → `clamp_effort` plus narrow
   tables for Kimi K3 / GLM-5.2 / GLM-5.3; `openai-codex` skips the narrow table;
   unknown routes fall back to the widest OpenAI-compatible set.
4. **One Jev call per turn.** Memo key `(session_id, turn_id)`; `failed`/`unsupported` are
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
8. **No prompt storage, no prompt in logs/reasons/traces.** Reason strings carry effort
   values only. `command._ENTRY_FIELDS` is the only rendered session allowlist.
9. **Never write the operator's config from a chat command.** `/jev-auto-effort <mode>`
   sets an in-process `_MODE_OVERRIDE` only; the persist path is
   `plugins.entries.jev-auto-effort.settings.mode`.
10. **Schemas are a public contract:** `jev-auto-effort.status.v1`,
    `jev-auto-effort.probe.v1`. Field names and reason codes are not free to rename.
11. **Cache safety:** `cache_safety.effort_is_cache_safe()` returns `True` only for
    `chat_completions` / `codex_responses`; `anthropic_messages` and anything unknown →
    `False` (pin the session, never gamble the cache).

## 7. The Desktop GUI layer (commit `7d1f066`, undocumented elsewhere)

Three files, two tiers:

* **`plugin.yaml` → `config_schema`** — renders a settings form in Desktop
  Capabilities → Plugins. Saving writes `plugins.entries.jev-auto-effort.settings.<key>`,
  which `middleware._read_setting` reads back per call (not frozen at import). Types and
  `choices` **must** stay in sync with `middleware.DEFAULTS` / `VALID_MODES`; the
  settings writer refuses mismatches, and `tests/test_config_schema.py` enforces it.
* **`dashboard/plugin_api.py`** — FastAPI backend mounted at
  `/api/plugins/jev-auto-effort/`: `GET /status`, `POST /mode`, `POST /probe`. It reuses
  the already-loaded agent modules (`command._status_payload()`,
  `middleware.set_mode_override()`), so the chip reports exactly what
  `/jev-auto-effort status` prints. `probe` returns score/label/failure and a
  `text_chars` count only — **never the text**. It degrades to an `error: status_failed`
  payload rather than raising.
* **`desktop/plugin.js`** — opt-in desktop plugin (`defaultEnabled: false`): a status-bar
  chip `Jev <mode>`, a `Jev Effort` pane, and one palette command per mode plus a status
  command. Polls every 15 s. Shows `Jev —` when the backend is absent.

**Security boundary, not a bug:** the Python backend only mounts for plugins listed in
`plugins.enabled`. If the backend is genuinely absent or unreachable (for example the
plugin is disabled, or the dashboard half is not deployed), the chip correctly shows
`Jev —` rather than failing loudly.

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
* Tests import the payload as `hermes_plugin_jev_auto.<stem>`; inject fakes through
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
