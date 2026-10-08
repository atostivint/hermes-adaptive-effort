# Operator handoff

Documentation/source review: **2026-10-07, Europe/Paris**. Host deployments below are dated evidence, not current health checks.

## Start here

| Need | Reference |
| --- | --- |
| Install and try routing | [Project README](../README.md) |
| Configure providers, keys or sharing | [Configuration](CONFIGURATION.md) |
| Understand modes/status and diagnose a problem | [Usage](USAGE.md) |
| Understand implementation and guarantees | [Design](DESIGN.md), [Contracts](CONTRACTS.md), [Compatibility](MODEL_COMPATIBILITY.md) |
| Change the source | [AGENTS.md](../AGENTS.md), [Development](DEVELOPMENT.md), [CI](CI.md) |
| Recover previous operator context | [Preserved 2026-10-07 handoff](handoff-snapshot-2026-10-07.md) |

## Source and publication evidence

The repository is `atostivint/hermes-adaptive-effort`; its default branch is `master`. Release **v0.3.0** is the first tagged GitHub release and is marked as a **pre-release**; its [release notes](releases/v0.3.0.md) list the shipped features, contract changes and validation limits. Check the GitHub Releases page and `plugin.yaml` for the current version rather than this sentence.

That source includes five scorer providers, the four public modes, route-specific named effort choices, guarded Anthropic per-message continuity, per-conversation history, the opt-in target-model context/catalog, the compact Desktop interface, and the model-validation tooling with its 2026-10-07 reports. Parent and child modes default to `off`. Exact runtime behavior belongs to Contracts and the implementation.

Earlier "not pushed", "unpublished" or "local work in progress" statements about these features are superseded: they are committed and published with v0.3.0. They remain in the archive as historical statements. Publication of source does not establish that every host has reloaded it.

## Dated deployment and validation records

| Record | Evidence and boundary |
| --- | --- |
| Prior operator handoff | Iris/Windows rollout, recovery paths, process-reload caveats and test results recorded at their original revisions; [archive](handoff-snapshot-2026-10-07.md) |
| Iris use cases, 2026-10-03 | Completed individual Codex/Go requests and request-field observations; [report](reviews/live-iris-use-cases-20261003.md) |
| Local scorer trial, 2026-10-05 | Kev 0.8B/4B synthetic-label agreement and cold/warm behavior; [report](reviews/local-scorer-benchmark-20261005T090911Z.md) |
| Model compatibility | Exact route registry, vendor references and limits of local/live evidence; [matrix](MODEL_COMPATIBILITY.md) |

No fresh inspection of Iris, Windows's installed plugin, the gateway or Desktop was performed for this documentation refresh. Use the archive's deployment/recovery notes as context, then inspect the actual serving host before asserting that its state is current.

## Features published in v0.3.0

Route-specific named effort choices keep numeric `0..2` scoring for unknown vocabularies and `/hae probe`. Jev, OpenAI Decisions, Cloudflare and both custom formats have strict named-choice request/response contracts; OpenRouter/custom Chat Completions require exactly `{"effort":"<allowed level>"}`. Named values outside the route list fail open without a second scorer call. Status and Desktop decision events use schema v2 with decision type, allowed choices and cache behavior; the Desktop reader accepts both v1 and v2 during transition.

The native Anthropic path is limited to exact registered models on HTTPS `api.anthropic.com`. For the five per-message IDs, it merges Anthropic's dated beta into an already-exposed `anthropic-beta` header and replays effort markers before matching user turns while leaving top-level effort unchanged. The marker registry retains levels and hashed positions only. Without the header, `auto` pins per route and `always` uses top-level effort with a visible cache-reset caveat. Compression, missing anchors, manual initial-setting changes, reset/eviction and errors invalidate continuity. Local tests cover generated payloads; live API acceptance, cache effects, cost and response quality remain unmeasured.

The Desktop identity fix matches the chip and REST lookup to the stored conversation ID (middleware's `agent.session_id`), not the SDK's temporary `focusedSessionId`, with owner-scoped `session.info` mappings for rotations. Native selector writes use the runtime ID and require an acknowledgment naming both IDs. Events add `conversation_id` and retain the historically mislabeled `runtime_session_id` alias for compatibility. Hosts need the Desktop extension reloaded; already-running agent processes can keep sending the older event shape.

The per-conversation history journal stores prompt-free metadata for applied effort changes under the Hermes profile and clears one conversation on explicit reset. No live scorer or cache benchmark was run; the cache label is route evidence, not a measured savings claim.

The live model validation plan (`docs/plans/live-model-effort-validation.md`), runner/probe scripts and campaign reports under `docs/validation/runs/` are published with v0.3.0. The 2026-10-07 [campaign report](validation/runs/live-20261007/report.md) records a partially executed Codex campaign with deterministic local classifier scores: only `gpt-6.1-sol` has complete `low/medium/high` evidence; other Codex models are partial or unproven, and OpenRouter/Go generation cases were blocked before send. Paid calls were not repeated after the final probe hardening. This is neither a live scorer evaluation nor exhaustive compatibility proof.

Release verification for v0.3.0 reran the Python suite, the Node Desktop scenarios and Ruff locally, then required the GitHub CI matrix and Security checks before merge. No new paid model call was made for the release. Deployment to Iris and the Hermes catalog submission are outside this release.

The Decisions and target-context plans describe implemented source paths. Their live scorer/quality/cost evaluations remain separate work, as recorded in their [design records](README.md#implemented-design-records).

The 2026-10-07 fallback fix has two parts. An ineligible request after a scored primary now reports its own route as `unsupported`, clears the unapplied target, and retains the label for a later eligible request. OpenCode Go `mimo-v2.6-flash` on Chat Completions is now an exact registered route: it injects top-level `reasoning_effort` with low/medium/high when no field exists and reuses the primary's turn decision on fallback. The [dsh-opencode-go maintainer's direct Go probes](https://github.com/Duskriver/dsh-opencode-go/blob/6a834cfda0f4db4e08243b40883eec71abddf655/docs/verification.md#issue-28-mimo-reasoning-controls-2026-10-02) returned HTTP 200 for these three values; OpenCode Go's catalog still declares `reasoning_options = []`. Xiaomi's Responses documentation says all non-`none` values enable the same reasoning behavior. No Hermes live Go request or graded-intensity/cost difference was measured for this plugin.

## Operational follow-up

- Ox Alpha / `x-preview-f-free` is a retired route. Keep its earlier rejection observations in historical reports rather than current user-facing warnings. The operator reported on 2026-10-07 that it had been unavailable for at least a month; it is absent from the [current Zen model table](https://opencode.ai/docs/zen/#endpoints). The exact removal date was not independently established.
- Treat provider availability, scorer quality, cache effects and net savings as separate measurements.
- Distinguish route acceptance from reasoning-budget changes and answer quality.
- Validate host compatibility after Hermes updates because effort mapping and some integration paths use lazy internal imports.
- Follow the operator's backup policy for the archived recovery locations; this refresh did not inspect or clean them.

## Maintenance rule

Keep this file focused on source/publication evidence, dated deployments and outstanding operator work. Link to current guides for behavior. Preserve dated findings rather than updating old observations into present-tense claims.
