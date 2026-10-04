# Handoff — `hermes-adaptive-effort`

**Operator snapshot, recorded 2026-10-03.** Start with [README](../README.md) for user guidance, [runtime contracts](CONTRACTS.md) for behavior, [design choices](DESIGN.md) for rationale and [development](DEVELOPMENT.md) for contributor setup. Historical evidence is indexed in [docs/README.md](README.md).

This file is the single entry point for an agent picking the work up cold. It states
what the project is, what is verified today, what is *not* finished, and recorded operational gaps. Host state below is dated evidence, not a fresh health check.

---

## 1. What this is

A Hermes plugin that lets a selected external rubric scorer choose the
**reasoning effort** of each LLM request. Jev (TypeSafe) remains the default;
OpenRouter and Cloudflare are explicit opt-in providers; Cloudflare's `clef` and `clef-flash`
models are selectable. Prompt sharing also requires `prompt_sharing_provider` to match the
selected scorer.

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
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort' --enable
```

(the `#subdir` fragment points at the renamed payload directory inside the repository).

## 2. Verified state (2026-10-04)

The payload directory, plugin ID, slash command, dashboard route and Desktop identity are
`hermes-adaptive-effort`. Jev remains the default scorer. OpenRouter is explicit opt-in,
requires a configured model and `OPENROUTER_API_KEY`; Cloudflare requires a valid account ID,
`CLOUDFLARE_AUTH_TOKEN`, and can select `clef` or `clef-flash`. Neither has a silent fallback
to Jev. Prompt text is not sent until the matching `prompt_sharing_provider` opt-in is set.
OpenRouter requests require ZDR endpoints and deny data-collecting endpoints; the plugin cannot
assure ZDR for Jev or Cloudflare.

At the 2026-10-03 baseline, the Windows test suite passed **217 tests** and Ruff passed.
The prompt-consent and Cloudflare model-selector changes were not tested in this checkout.
GitHub `master` contains the implementation; Iris was reinstalled from the GitHub subdirectory
on 2026-10-04.

### Current host installs

**Windows client.** `%LOCALAPPDATA%\hermes\plugins\hermes-adaptive-effort` points to
`%USERPROFILE%\Projets\jev-auto-effort\hermes-adaptive-effort`. The Desktop plugin link
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

### Documentation delivery

The documentation refresh is also available in a separate master checkout at `/root/workspace/Hermes/hermes-adaptive-effort` on Iris. The older `/root/workspace/Hermes/jev-auto-effort` checkout was left on its existing feature branch, including its local `.hermes/` directory. The Windows client workspace contains the same documentation. Documentation-only updates do not require a runtime restart.

### Git and repository identity

`codex/windows-desktop-dev-loop` was merged into `master` as `d1d31c2` and pushed to
`https://github.com/atostivint/hermes-adaptive-effort.git`. The code payload directory is
renamed. This environment's checkout path itself remains
`%USERPROFILE%\Projets\jev-auto-effort`; keep that configured workspace path intact when
using this session.

The repo was previously `atostivint/jev-auto-effort`; Iris metadata and this handoff use
the new repository slug. For future installs, use:

```bash
hermes plugins install 'atostivint/hermes-adaptive-effort#hermes-adaptive-effort' --enable
```

## 3. Live configuration

Windows uses the default `mode: off`. Iris remains `mode: auto` with
`endpoint: https://api.typesafe.ai/v1/systemone`. Its plugin settings currently contain no
`scorer_provider`, `cloudflare_account_id`, `cloudflare_model`, or
`prompt_sharing_provider`; the effective scorer is Jev, and the consent default is `none`,
so no scorer prompt is sent. OpenRouter requires `scorer_provider: openrouter`, `scorer_model`,
and `prompt_sharing_provider: openrouter`. Cloudflare requires `scorer_provider: cloudflare`,
`cloudflare_account_id`, a `cloudflare_model` choice, and
`prompt_sharing_provider: cloudflare`.

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

## 5. Operational follow-up

- Full SQLite integrity for Iris's default `state.db` remains unverified after the Hermes updater warning. This is separate from plugin correctness; no database repair or replacement has been authorized here.
- The Windows checkout retains its historical folder name because the active workspace is rooted there. The GitHub slug and payload identity are renamed.
- Existing Windows/isolated Desktop agent processes need a restart or reconnect to load the renamed plugin.

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
   `invalid_prompt`, `prompt_consent_required`, `credential_missing`, `http_error`,
   `timeout`, `transport_error`, `malformed_response`, `unexpected_error`,
   `classifier_error`, plus scorer-selection codes `model_missing`, `account_missing`,
   `account_invalid` and `unsupported_provider`.
7. **Subagents** are classified from the parent-written goal (`subagent_start`), gated by
   the independent `subagent_mode`. `status` performs no classification; `probe` scores only operator-typed text and stores no decision.
8. **Prompt sharing requires provider-specific consent.** `prompt_sharing_provider`
   must match the selected scorer before any scorer request is made; otherwise the
   decision fails open with `prompt_consent_required`. `prompt_chars` only caps the
   excerpt and says nothing about provider retention. OpenRouter requests require
   ZDR endpoints and deny data-collecting endpoints. This plugin makes no ZDR
   guarantee for Jev or Cloudflare. The plugin itself does not persist prompts or
   include them in logs/reasons/traces. Effort-change notices log only old and new
   effort values; `command._ENTRY_FIELDS` is the only rendered session allowlist.
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
  the global latest transition, and one toast per newly observed applied change. Toasts apply across conversations because the feed has no session id.
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
`Effort: N/A` rather than failing loudly.

## 8. Product limitations and next evaluations

1. **Ox Alpha / `x-preview-f-free`:** `medium` returns HTTP 400. This is a **known gap,
   deliberately not worked around** — silently remapping it would hide a real vendor
   rejection. Either document it further or make it fail open loudly.
2. **Cost effect is unmeasured.** No live A/B has been run, so every cost or cache claim
   in the README is an expectation, not a measurement. Do not restate them as results.
3. **Cloudflare Clef / Clef Flash:** the existing Clef adapter had network-free tests; the new Flash selector was not tested in this checkout. Live latency, scoring quality and cost evaluation remain pending; Iris has no Cloudflare account ID setting and no matching prompt-sharing consent, so no live request was sent.
4. **OpenRouter evaluation:** OpenRouter ZDR routing is requested per call; no live scorer request or model comparison has been run.
5. **Pre-install backups** sit in `~/.hermes/cache/scratch/` (`jev-backup-20260930-080501`,
   `jev-backup-20260930-082258`). Harmless, and they are the rollback path if the managed
   install ever needs undoing. Retain or remove them only under the operator's backup policy.

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
* Behaviour change ⇒ update `README.md`, `docs/CONTRACTS.md` **and** this file.
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

## 11. Automated-check rollout (2026-10-03)

[PR #1](https://github.com/atostivint/hermes-adaptive-effort/pull/1) added CI/security workflows and fixed the first CodeQL finding: the mode API now exposes only `persist_failed` on a persistent write failure, rather than exception details. The sensitive-exception regression test passes. Combined source revision `860793d` passed all 217 tests on Ubuntu/Python 3.11, 3.12 and 3.14 and Windows/Python 3.12, plus lint/workflow/syntax and security jobs. CodeQL had no open findings for master after analysis.

Secret scanning, push protection, Dependabot security updates and weekly update PRs are enabled. The full-history scan and declared-dependency audit passed. See [CI documentation](CI.md) for scope and limits.

Iris's deployed `dashboard/plugin_api.py` was replaced atomically from the master source checkout, with its prior file backed up under `/root/.hermes/cache/scratch/adaptive-effort-ci-20261003/`. Its deployed SHA-256 is `34407390bbea75cbb78ef1d8cd39fb7c34e29de068a66821fee7fa0255ba11f1`. The gateway was restarted and is active. Windows's installed plugin junction resolves to the updated local source; already-running isolated Desktop agent processes may need to reconnect/restart to reload this backend module. No database repair or live scorer call was performed.
