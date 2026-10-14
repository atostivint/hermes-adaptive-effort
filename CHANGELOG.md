# Changelog

All notable changes to Hermes Adaptive Effort are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- OpenRouter scoring uses OpenRouter's Decisions API (`POST /api/alpha/decisions`) with the shared System One rubric, so `scorer_model` must name an OpenRouter decision model (for example `typesafe/jev-1.13`). `provider: {zdr: true, data_collection: "deny"}` is still sent; OpenRouter's Decisions request schema documents `provider` preferences including `zdr` and `data_collection`; no live OpenRouter call was made (#48)
- Scorer calls that end in a transient HTTP status (408, 429, 500, 502, 503, 504) are retried once inside the existing `timeout_s` deadline (at least 300 ms must remain; `Retry-After` honoured only if it fits, else a 100 ms wait); timeouts, transport errors, other statuses and received answers are never retried, and the breaker and latency histogram see one call
- Scorer deadline is now a per-provider default (`timeout_s: 0`): Jev 3.0 → 2.0 s, custom 3.0 → 10.0 s, others 3.0 s; installs that never set `timeout_s` move automatically, a positive value still wins, and configuration is never rewritten
- Desktop status chip drops the redundant `Effort: ` prefix from its visible label (the icon already identifies it); tooltip, details popover, notifications and the terminal status item are unchanged

### Removed

- **Breaking:** the custom `chat_completions` API format and OpenRouter chat-model scoring. A configured `custom_api_format: chat_completions` now fails open with `unsupported_api_format` and makes no network call; an OpenRouter `scorer_model` that is a chat model fails open on the Decisions API. Move to a `systemone` endpoint or an OpenRouter decision model (#48)

### Added

- `tool_error_escalation` (default `true`, on by default): when the same tool error repeats after a `write_file`/`patch` attempt within one user turn, effort rises one level within the route's allowed choices for the rest of that turn, at most once per error and never to `none`/`ultra`. It is a local signal only (no scorer call; only a SHA-256 of the normalised error is compared and kept) and it only raises effort on exact cache-safe dynamic routes; Claude routes and every other route are unchanged. Set `tool_error_escalation: false` to restore the previous behaviour; the decision entry gains additive `escalated` / `escalation_reason` fields (#34)
- `debug` setting (default `false`): Desktop selector-sync notices gain a diagnostic code and counts (for example `route_ambiguous`, `route_none`, `routes_timeout`) explaining why "The chat owner could not be resolved safely" was shown, each sync step is logged to the Desktop developer console, and `/hae status full` reports `debug=on|off`. Diagnostics never include identifiers, prompts, paths or hosts
- Optional continuation hysteresis for `always` and `auto` on exact dynamic routes: `continuation_downgrade_votes` (default `0` = off, maximum 5, suggested 2; active only with `dialogue_context_messages` above 0) sends a separate yes/no continuation question (`noul`; OpenAI Decisions `predicate`) in parallel with the unchanged effort call under the same `timeout_s` deadline. On a continuation a higher choice applies at once and a lower one only after that many consecutive lower votes; an unanswered or malformed continuation answer is ignored. Decision entries gain `continuation` and `held`; off, or without dialogue context, no second call is made and request bodies are byte-identical. Wire shapes come from public provider documentation and no live scorer call was made (#32)
- Optional bounded dialogue context for the scorer: `dialogue_context_messages` (default `0` = off, maximum 6) also sends up to that many earlier user/assistant text messages, each capped at 1,200 characters, as labelled untrusted context. System/developer messages, tool calls and results and non-text parts are never sent, subagents and `/hae probe` never carry it, and with the default every scorer request body is byte-identical to before (#31). No live scorer call was made
- `clef-omni` Cloudflare model selector (`@cf/cloudflare/clef-omni`) for the `cloudflare` scorer; unknown selectors still fall back to `clef`. The model id matches Cloudflare's Workers AI model page; no live Cloudflare call was made
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
