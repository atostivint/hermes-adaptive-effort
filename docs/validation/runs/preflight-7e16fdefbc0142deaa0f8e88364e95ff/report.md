# Local validation preflight — 2026-10-07

This initial preflight did not read a Hermes config path and is superseded for OpenRouter inventory by the [configured-route preflight](../config-inventory-308131f421724ce88c893f365a91fd7b/report.md).

## State

The local inventory and manifest checks completed. The live campaign is blocked before sending any request because the runner does not yet integrate a pre-send guard with every physical Hermes transport request, cannot observe the actual HTTP request body, and has no reconciled provider-account counters. No target model or scorer model was called.

## Frozen evidence

- Campaign ID: `150f2fd5-303c-4d06-b41e-0e1e65a0e8d0`
- Manifest: `manifest.json`
- Manifest SHA-256: `410cc1b09075e0063ff814bfa6eeb09bf8cabbaf419c06e74b733a3e934b8eca`
- Manifest validation: valid; Hermes source fingerprint verified
- Go routes: 13 exact registry routes, 39 score mappings, 33 distinct model/wire pairs
- OpenRouter: not inventoried; no config path was supplied/found (`openrouter_config_read: false`), so configured exact routes remain unknown and blocked
- Classifier origin: controlled; real scorer calls: 0
- Run outcome: blocked; 39 sanitized result rows; physical requests: 0
- Zero-send resume check: first `run` and subsequent `run --resume` each returned blocked; ledger kept two history snapshots, two invocation entries, zero attempt records, zero requests, and 39 rows with null attempt IDs
- Starting and ending OpenRouter/OpenCode Go dollar account counters: not read; no cost is reported as zero. Codex quota status is recorded in the later configured-route audit.
- Route pricing and enforceable per-call maximums: unavailable; no route is paid-run eligible
- GLM-5.2 local mapping: low/medium/high all produce `high`; this is a mapping limitation
- The versioned synthetic corpus is preparatory and marked `consumed_by_runner: false`; the blocked runner currently stores only the matching answer oracle `4`

The manifest lists local mapping readiness separately from live eligibility. Every case has `live_status: blocked`, `pricing_status: unavailable`, and no maximum-cost bound. `plugin_sha256` fingerprints local payload Python files only and does not prove which plugin artifact a Hermes runtime loaded.

## Checks

- `scripts/run_tests.ps1` with a fresh writable temp root: 526 passed
- `scripts/run_lint.ps1`: passed
- `inventory`: 39 cases, zero network requests
- `check`: valid, 39 mappings, 33 distinct wire pairs, zero network requests, `run_ready: false`
- `run` then `run --resume`: both blocked before send; zero network requests and zero scorer calls; resume preserved the first result set and appended history

No operator billing settings or model effort policy were changed. No cost or compatibility conclusion is claimed from this zero-send preflight.

> **Redaction, 2026-10-07 (v0.3.0 release review):** `hermes_source.path` in `manifest.json` was replaced by `<HERMES_SOURCE_ROOT>` to remove a local user path before publication. The recorded `manifest_hash`/SHA-256 values were computed over the original file and are intentionally left unchanged, so `check` now reports `manifest_hash_mismatch` for this historical manifest. No other field was edited.
