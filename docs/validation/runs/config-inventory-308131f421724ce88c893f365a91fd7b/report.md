# Configured-route preflight — 2026-10-07

> Historical preflight snapshot: its Codex access and account-match findings were superseded by the later [Codex quota and catalog audit](../codex-zero-send-audit-20261007/report.md). The blocked local runner results below remain the record of that preflight run.

## State

The configured-route inventory and manifest check completed locally. The live campaign is blocked before any send. The runner has no guard integrated immediately before each physical Hermes transport send, cannot observe the actual HTTP body, and cannot reconcile provider account counters. No target model or scorer model was called.

## Frozen evidence

- Campaign ID: `edbaa89c-e645-436e-b166-0a845909eab4`
- Manifest: `manifest.json`
- Manifest SHA-256: `76ef9632f39dbe18c24c450abdd352236d01889d7a855f1601b20e05d446ff8f`
- Manifest check: valid; Hermes source fingerprint verified; `run_ready: false`
- OpenCode Go: 13 exact routes, 39 score mappings, 33 distinct model/wire pairs
- OpenRouter: 2 exact configured model IDs, both blocked due to no verified usable effort route: `anthropic/claude-opus-4.8` and `deepseek/deepseek-v4-pro`
- All 45 cases: `live_status: blocked`; no price or maximum-cost bound is claimed
- GLM-5.2 mapping: low/medium/high all produce `high`
- Classifier origin: controlled; real scorer calls: 0
- Starting and ending OpenRouter/OpenCode Go dollar account counters: not read; actual cost remains unknown, never reported as zero. Codex quota status is recorded in the later audit linked above.
- First `run` and subsequent `run --resume`: both returned blocked; 0 requests, 0 attempt records, 45 sanitized rows; resume retained two ledger history entries
- Synthetic corpus: preparatory only (`consumed_by_runner: false`); no prompt was sent

The manifest and ledger contain no prompt text, headers, raw errors, response bodies, session identifiers, or credential values. `plugin_sha256` covers local payload Python files, not proof of which plugin artifact a Hermes runtime loaded.

## Checks

- `scripts/run_tests.ps1` with a fresh writable temp root: 526 passed
- `scripts/run_lint.ps1`: passed
- `inventory --config <normal Hermes config>`: 45 cases; 0 network requests
- `check`: valid; 39 Go mappings; 33 distinct pairs; 0 network requests; `run_ready: false`
- `run` then `run --resume`: both blocked before send; 0 network requests and 0 scorer calls

No operator billing settings or effort policy were changed. No model compatibility or cost conclusion is claimed from this zero-send preflight.

> **Redaction, 2026-10-07 (v0.3.0 release review):** `hermes_source.path` in `manifest.json` was replaced by `<HERMES_SOURCE_ROOT>` to remove a local user path before publication. The recorded `manifest_hash`/SHA-256 values were computed over the original file and are intentionally left unchanged, so `check` now reports `manifest_hash_mismatch` for this historical manifest. No other field was edited.
