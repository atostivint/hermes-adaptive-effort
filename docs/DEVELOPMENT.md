# Development

Read [AGENTS.md](../AGENTS.md) before editing. [Design](DESIGN.md) explains the architecture, [Contracts](CONTRACTS.md) defines behavior, and [CI](CI.md) describes hosted checks. Dated operator deployments belong in [Handoff](HANDOFF.md).

## Set up and run checks

The repository root is the Hermes plugin payload, not a pip-installable package. Install development dependencies into the project virtual environment. Hermes source must be importable for effort mapping and real dispatcher integration.

Linux / macOS:

```bash
./scripts/bootstrap_test_env.sh
./scripts/run_tests.sh
./scripts/run_lint.sh
```

Windows PowerShell:

```powershell
.\scripts\bootstrap_test_env.ps1
.\scripts\run_tests.ps1
.\scripts\run_lint.ps1
```

Bootstrap creates the environment, installs the pinned development requirements and runs the suite. Use it on a fresh machine; use the test/lint scripts for subsequent checks.

The Linux test script uses `.venv/bin/python`. The Windows script uses `.venv/Scripts/python.exe` and discovers Hermes from `HERMES_SOURCE_ROOT`, `$env:HERMES_HOME\hermes-agent`, a sibling checkout, or `$env:LOCALAPPDATA\hermes`. It falls back to a scratch pytest directory if the normal temporary/cache directories have incompatible permissions.

Set `HERMES_SOURCE_ROOT` to a checkout containing `agent/reasoning_effort.py` if discovery fails. `tests/conftest.py` adds that source to the import path. Do not install the payload itself.

The Desktop behavior harness uses Node's built-in test runner:

```powershell
node --test tests/desktop_decision_event_behavior.test.mjs
```

CI runs this harness alongside the JavaScript syntax check; see [CI](CI.md#functional-checks-ci).

## Source map

| Area | Responsibility |
| --- | --- |
| `plugin.yaml`, `__init__.py` | Manifest/settings schema and registration of middleware, commands and lifecycle hooks |
| `middleware.py` | Gates, turn/route decisions, request copies, child registry, applied changes and Desktop events |
| `effort.py`, `cache_safety.py` | Named choice/legacy score mapping, exact route registry and transport cache evidence |
| `scorers.py`, `*_client.py` | Explicit provider selection, credentials, endpoint display and scorer adapters |
| `rubric.py` | Shared named-choice and legacy score questions, guidance and strict response validation |
| `model_profiles.py`, `model_profiles.json` | Local exact-ID reference catalog and bounded optional context |
| `command.py` | `/hae` commands and public status allowlist |
| `dashboard/`, `desktop/` | REST backend, focused-chat status, notifications and selector synchronization |
| `tests/`, `scripts/`, `.github/` | Local verification, test environments and hosted workflows |

The [architecture diagram](DESIGN.md#components) shows how these areas connect.

## Test contracts

Tests import the payload as `hermes_plugin_adaptive_effort.<stem>` through `import_plugin()`. A session autouse fixture blocks socket creation; inject transports/key readers or `_classifier_factory` instead of making real HTTP requests.

A function autouse fixture resets plugin state and substitutes empty settings, so tests never depend on the operator's Hermes configuration.

| Test area | Modules |
| --- | --- |
| Request preservation, modes, turn/route reuse and concurrency | `test_middleware.py`, `test_command_modes.py`, `test_turn_scope.py`, `test_decision_cache.py`, `test_cache_safety.py` |
| Mapping, rubric and exact model context | `test_effort.py`, `test_rubric.py`, `test_model_profiles.py` |
| Named choices, scorer formats, exact route options and Claude turn markers | `test_named_effort_choices.py`, `test_native_choice_middleware.py` |
| Scorer construction and transports | `test_scorers.py`, `test_jev_client.py`, `test_openai_decision_client.py`, `test_openrouter_client.py`, `test_cloudflare_client.py`, `test_custom_client.py` |
| Child lifecycle and routing | `test_subagent.py` |
| Commands, manifest/settings parity and real host integration | `test_command.py`, `test_plugin_registration.py`, `test_config_schema.py`, `test_dispatcher_integration.py` |
| Bounded prompt-free conversation history and CLI rendering | `test_history_store.py` |
| REST/Desktop state, events and session selector synchronization | `test_plugin_api.py`, `test_desktop_decision_events.py`, `test_desktop_decision_event_contract.py`, `desktop_decision_event_behavior.test.mjs` |
| Historical review regressions | `test_review_fixes.py` |

Keep test counts in dated result records, alongside revision, platform and command. This inventory describes contracts rather than a total that drifts with every change.

## Releasing

- **Version source of truth**: `plugin.yaml` carries the canonical version. `dashboard/manifest.json` and CHANGELOG.md must match; `test_version_parity.py` verifies alignment.
- **CHANGELOG discipline**: Every user-visible change lands as an entry in the `## [Unreleased]` section. Empty Unreleased blocks fail the bump step.
- **Two-step release flow**:
  1. `./scripts/release.sh prepare X.Y.Z` creates a release branch, runs `python .github/scripts/release_notes.py bump X.Y.Z`, runs tests, and opens a PR.
  2. After merge to master, `./scripts/release.sh tag X.Y.Z` tags the commit and pushes the tag. The Release workflow then publishes the release.
- **Pre-release convention**: versions with major version 0 or a prerelease suffix (e.g., `-rc.1`) are marked `--prerelease` on GitHub.
- **Optional long-form notes**: if `docs/releases/vX.Y.Z.md` exists, the release notes helper appends a link to it. Validation limits and breaking changes are good candidates for that file.
- **Deployment**: remains a manual operator step on each host (see [Handoff](HANDOFF.md)). Workflows do not deploy.

### Real dispatcher integration

`test_dispatcher_integration.py` creates a throwaway `HERMES_HOME`, copies the payload into its plugins directory, invokes Hermes' `PluginManager.discover_and_load()` and enters through `apply_llm_request_middleware`.

It checks actual registration/dispatch, one decision through a tool loop, re-clamping on route changes, fail-open scorer errors, unsupported controls without scoring, off-mode metadata and the Decisions adapter path. Both standard test scripts run it; it must not be silently skipped when Hermes source is missing.

## Adding target-model profiles

Edit `model_profiles.json`. Runtime requests never fetch documentation, and lookup uses exact case-sensitive model IDs.

Each profile has a unique `id`, `vendor`, nonempty `model_ids`, `effort_levels`, nullable `default_effort`, a concise `summary` (1 to 360 characters), HTTPS `source` and `reviewed` date (`YYYY-MM-DD`).

Reuse a row only when the documented levels, default and semantics apply to every ID. Otherwise create a separate profile. Use a null default when the source specifies none. An empty levels list means no discrete levels are documented; explain whether the source describes a thinking toggle, no effort control or a non-generative task. It does not establish unsupported transport behavior.

Keep vendor documentation separate from proxy-route support. For provider-specific IDs with an unidentified upstream lab, cite the provider's listing and state that attribution is unknown. Date catalog snapshots rather than presenting them as a live model directory.

A data-only profile addition must not change clamping, injection eligibility or dynamic status. `test_model_profiles.py` covers validation, matching, bounded serialization, adapter formats and profile growth.

## Documentation maintenance

- Keep installation short in the root README; place detailed setup and workflows in Configuration and Usage.
- Update Contracts and Compatibility when implementation behavior or exact route evidence changes. Update Handoff when source/publication or operator state changes.
- Keep historical reports intact and date new observations. A passing local test is not live provider acceptance or a cost/quality result.
- Check relative links/anchors, examples against settings defaults and the three Mermaid diagrams after documentation edits.
- Keep contributor invariants in AGENTS and tool configuration in `pytest.ini`/`ruff.toml`. A root `pyproject.toml` would make Hermes treat the plugin as a managed runtime member.
