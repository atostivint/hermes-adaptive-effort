# Changelog

All notable changes to Hermes Adaptive Effort are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
