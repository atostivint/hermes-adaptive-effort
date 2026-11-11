# Domain: ui-surfaces

The two optional surfaces shipped with the plugin: Hermes Dashboard backend and Hermes Desktop extension.

## Owns
- `dashboard/plugin_api.py` - backend at `/api/plugins/hermes-adaptive-effort/`, a thin wrapper over
  `command.py` / `middleware.py` (status, probe, mode, changes).
- `dashboard/manifest.json` - name, version, label, icon, tab. **Version must equal `plugin.yaml`**
  (parity test `test_version_parity.py`).
- `desktop/plugin.js` - plain ESM, no build step, only `@hermes/plugin-sdk`, `react`,
  `react/jsx-runtime` resolve. Sends the Desktop decision event v2.
- `desktop/.hermes-package.json`, `assets/icon.png`.
- `tests/test_plugin_api.py`, `tests/test_desktop_decision*.py`,
  `tests/desktop_decision_event_behavior.test.mjs`.

## Invariants
- Both surfaces fail open: backend unreachable renders `N/A`, actions toast, nothing throws into the host.
- Desktop readers accept decision event **v1 and v2** during transition; probe stays v1 and the
  anonymous change feed stays v1.
- `show_desktop_popup` gates the chip, the mode popup and its notifications; `show_tui_status` gates the
  terminal item. Display flags never affect classification.
- Never send prompt text, keys or session ids to these surfaces; effort values only.
- Never bump the version by hand: only `scripts/release.ps1` / `release.sh` moves it, in both files.

## Known traps
- `dashboard/manifest.json` and `plugin.yaml` are a pair; editing one alone breaks the parity test.
- The Desktop half is copied app-level by the main process: paths inside `plugin.js` must stay relative.
- The `.mjs` contract test is not pytest; run it explicitly if you touch `plugin.js`.

## Proof
- `.\scripts\run_tests.ps1 -q -k "plugin_api or desktop or version_parity"`.
- `.\scripts\run_lint.ps1`.
- Nobody can see the Desktop UI from here: say `gaps: Desktop not rendered` rather than "looks fine".

## Open threads
- v1 decision-event support is transitional and must stay until the operator drops old clients.
