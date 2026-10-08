# Documentation

Start with the [project README](../README.md) to install the plugin and try a routing mode.

## Use the plugin

| Guide | What it covers |
| --- | --- |
| [Configuration](CONFIGURATION.md) | Five scorer providers, credentials, local models, settings and data sharing |
| [Usage and troubleshooting](USAGE.md) | Commands, mode examples, Desktop/terminal behavior and missing-effort diagnostics |
| [Model compatibility](MODEL_COMPATIBILITY.md) | Exact routes, registered controls and the limits of vendor/local/live evidence |

## Understand and maintain it

| Reference | What it covers |
| --- | --- |
| [Design and architecture](DESIGN.md) · [Version française expliquée](DESIGN.fr.md) | Component diagram and reasons behind the routing/safety choices |
| [Runtime contracts](CONTRACTS.md) | Decision scopes, tool-loop sequence, controls, errors, schemas and APIs |
| [Development](DEVELOPMENT.md) | Source layout, profile maintenance, reproducible checks and release process |
| [Automated checks](CI.md) | GitHub Actions, security checks, coverage and release workflow |
| [Contributor instructions](../AGENTS.md) | Rules and invariants for implementation changes |
| [Operator handoff](HANDOFF.md) | Current source reference, dated rollout evidence and open operational work |
| [Release notes v0.3.0](releases/v0.3.0.md) | First tagged pre-release: features, contract changes and validation limits |
| [Changelog](../CHANGELOG.md) | All releases and unreleased changes in Keep a Changelog format |

Code defines current behavior. Operator host observations are dated snapshots, not installation prerequisites or fresh health checks.

## Implemented design records

These plans preserve their implementation rationale. They are not pending provider integrations.

| Record | Status |
| --- | --- |
| [OpenAI Decisions](plans/openai-decision-api.md) | Adapter is implemented; live scorer evaluation remains separate |
| [Target-model scoring context](plans/target-model-scoring-context.md) | Opt-in context and local catalog are implemented; quality/cost evaluation remains separate |

## Validation records

| Record | Boundary |
| --- | --- |
| [Live model effort validation plan](plans/live-model-effort-validation.md) | Campaign design, guards and spending envelopes |
| [Live campaign report, 2026-10-07](validation/runs/live-20261007/report.md) | Partially executed Codex campaign with deterministic local scores; complete `low/medium/high` evidence only for `gpt-6.1-sol`; OpenRouter/Go cases blocked before send |
| [Codex quota and catalog audit, 2026-10-07](validation/runs/codex-zero-send-audit-20261007/report.md) | Access and quota record; no compatibility result |

## Work in progress

| Record | Boundary |
| --- | --- |
| [Catalog submission draft](catalog-submission/pr-description.md) | Entry and PR text pinned to `master` commit `c058b8a` (declares 0.3.0, after the v0.3.0 tag); validated locally, not yet submitted to `NousResearch/hermes-agent` |

## Historical evidence

Preserve original findings, names, paths and test counts in these records. They describe their dates and revisions; they are not current setup instructions.

| Record | Context |
| --- | --- |
| [Announcement draft, 2026-10-05](announcements/linkedin-post-2026-10-05.md) | **Obsolete** French promotional draft; describes a retired separate scorer-consent step; do not reuse |
| [Preserved operator handoff, 2026-10-07](handoff-snapshot-2026-10-07.md) | Full earlier rollout/recovery narrative, including statements superseded by the current handoff |
| [Original delivery handoff](handoff-t_cb5d47d0.md) | Earlier plugin identity and initial acceptance evidence |
| [DeepSeek review, 2026-09-29](review-deepseek-2026-09-29.md) | Review of the then-installed host/plugin |
| [Review follow-up, 2026-09-29](review-followup-2026-09-29.md) | Fixes and installation state at that date |
| [Iris use cases, 2026-10-03](reviews/live-iris-use-cases-20261003.md) | Completed route observations; linked raw captures are absent from this checkout |
| [Auto-injection route research](reviews/auto-injection-route-research.md) | Evidence considered when adding exact injection routes |
| [Auto-injection local validation](reviews/auto-injection-validation.txt) | Recorded local checks |
| [Model compatibility validation](reviews/model-compatibility-validation.txt) | Recorded mapping/control checks |
| [Muse injection validation](reviews/muse-effort-inject-validation.txt) | Recorded local checks |
| [Muse route probe](reviews/muse-effort-inject-probe.txt) | Probe outcomes and unsuccessful completion boundary |
| [Zenon local effort check, 2026-10-05](reviews/zenon-effort-compatibility-20261005.md) | Bounded local inventory and field acceptance without completed generations |
| [Local scorer benchmark, 2026-10-05](reviews/local-scorer-benchmark-20261005T090911Z.md) | Exploratory synthetic-label trial |

Documentation should distinguish implementation contracts, vendor documentation, dated live observations and unmeasured expectations. Recheck a historical claim before treating it as current.
