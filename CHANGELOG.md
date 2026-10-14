# Changelog

All notable changes to Hermes Adaptive Effort are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Scorer deadline is now a per-provider default (`timeout_s: 0`): Jev 3.0 → 2.0 s, custom 3.0 → 10.0 s, others 3.0 s; installs that never set `timeout_s` move automatically, a positive value still wins, and configuration is never rewritten

### Added

- Optional bounded dialogue context for the scorer: `dialogue_context_messages` (default `0` = off, maximum 6) also sends up to that many earlier user/assistant text messages, each capped at 1,200 characters, as labelled untrusted context. System/developer messages, tool calls and results and non-text parts are never sent, subagents and `/hae probe` never carry it, and with the default every scorer request body is byte-identical to before (#31). No live scorer call was made
- `clef-omni` Cloudflare model selector (`@cf/cloudflare/clef-omni`) for the `cloudflare` scorer; unknown selectors still fall back to `clef`. The model id comes from secondary listings and no live Cloudflare call was made
- Synthetic scorer evaluation: a 66-prompt corpus, a manual billed `scripts/run_scorer_eval.py` (accuracy, confusion matrix, p50/p95, failure codes per scorer) and a "Scorer evaluation" section in the Desktop popup backed by `GET /eval`
- Scorer circuit breaker: after `scorer_pause_failures` consecutive scorer failures the selected scorer is paused for `scorer_pause_s` seconds (reason `scorer_paused`, `scorer_pause` in `status json`)
- `/hae status` shows the selected scorer's p95 latency (`Scorer latency: p95 ≤ 350 ms (42 calls)`; `scorer_latency` in `status json`), from an in-process counts-only histogram
- `nace` scorer provider for the Nace Drex model (System One on `https://console.nace.ai/v1/systemone`), with the new `nace_model` setting (default pinned `drex-v2.0`), `NACE_API_KEY` (legacy `DREX_API_KEY` accepted) and an optional `NACE_BASE_URL` base. It supports named effort choices and the legacy score rubric, fails open, and never falls back to Jev. Live behaviour, score quality and cost are unmeasured
- `.env.example`; `.env` and `.env.*` are now git-ignored
- Per-conversation mode override (`/hae <mode> here`, `/hae default here`, Desktop "This conversation"), stored in the profile history database and leaving the default mode and config untouched

## [0.3.1] - 2026-10-08

### Added

- CHANGELOG.md in Keep a Changelog format with SemVer note
- Release scripts and workflow for tag-driven releases
- `.github/pull_request_template.md` and `.github/ISSUE_TEMPLATE/` (bug and feature forms)
- Test module `test_version_parity.py` validating plugin.yaml, dashboard/manifest.json and CHANGELOG.md alignment
- Coverage artifact collection in CI (informational, no threshold)
- `hermes-canary.yml` workflow: weekly Hermes master verification (non-blocking)

### Fixed

- dashboard/manifest.json version aligned with plugin.yaml (0.3.0)
- OpenCode Go `mimo-v2.6-flash` Chat Completions route is registered, so a fallback to it without an effort field receives `reasoning_effort` (`low`, `medium` or `high`) (#11)
- A fallback route without a usable effort control now reports `unsupported` for its own route and keeps the turn's decision for later reuse, without another scorer call (#11)

## [0.3.0] - 2026-10-07

First tagged pre-release with four routing modes, five scorer providers, and route-specific named effort choices.

- **Four routing modes**: `off` (default), `auto`, `once`, `always`, with independent subagent mode
- **Five scorer providers**: Jev (default), OpenAI Decisions, OpenRouter, Cloudflare Clef, and custom hosted/local endpoint
- **Route-specific named effort choices**: scorer picks directly from a route's allowed levels (e.g., `low/high/max`)
- **Anthropic per-message continuity**: exact registered native models on `api.anthropic.com` with guarded header extension
- **Per-conversation history**: bounded journal of applied effort changes (≤64 per conversation)
- **Desktop extension**: Effort chip with mode popup and recent changes
- **Status/Desktop schema v2**: decision type, allowed choices, cache behavior
- Opt-in model context for the scorer
- MIT license

See [full release notes](docs/releases/v0.3.0.md) for validation limits and contract changes.

[Unreleased]: https://github.com/atostivint/hermes-adaptive-effort/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/atostivint/hermes-adaptive-effort/releases/tag/v0.3.1
[0.3.0]: https://github.com/atostivint/hermes-adaptive-effort/releases/tag/v0.3.0
