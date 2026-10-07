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

The repository is `atostivint/hermes-adaptive-effort`; its default branch is `master`. At the start of this documentation refresh, local HEAD was `e1d7e99` (`feat: add model-aware scoring context and Decisions scorer`). A remote-ref check earlier on 2026-10-07 confirmed that same commit on GitHub.

That source includes five scorer providers, the four public modes, the opt-in target-model context/catalog, and the compact Desktop interface. Parent and child modes default to `off`. Exact runtime behavior belongs to Contracts and the implementation.

The prior handoff's "not pushed" statements about Decisions/context are superseded by the source/publication evidence above. They remain in the archive as historical statements. Publication of source does not establish that every host has reloaded it.

This documentation refresh covers documents and examples. Its checks do not refresh prior runtime test results or establish live provider behavior. Publishing it does not publish the separate local validation workstream below.

## Dated deployment and validation records

| Record | Evidence and boundary |
| --- | --- |
| Prior operator handoff | Iris/Windows rollout, recovery paths, process-reload caveats and test results recorded at their original revisions; [archive](handoff-snapshot-2026-10-07.md) |
| Iris use cases, 2026-10-03 | Completed individual Codex/Go requests and request-field observations; [report](reviews/live-iris-use-cases-20261003.md) |
| Local scorer trial, 2026-10-05 | Kev 0.8B/4B synthetic-label agreement and cold/warm behavior; [report](reviews/local-scorer-benchmark-20261005T090911Z.md) |
| Model compatibility | Exact route registry, vendor references and limits of local/live evidence; [matrix](MODEL_COMPATIBILITY.md) |

No fresh inspection of Iris, Windows's installed plugin, the gateway or Desktop was performed for this documentation refresh. Use the archive's deployment/recovery notes as context, then inspect the actual serving host before asserting that its state is current.

## Local work in progress

The current local implementation adds model-route-specific named effort choices while retaining numeric `0..2` scoring for unknown vocabularies and `/hae probe`. Jev, OpenAI Decisions, Cloudflare and both custom formats have strict named-choice request/response contracts; OpenRouter/custom Chat Completions require exactly `{"effort":"<allowed level>"}`. Named values outside the route list fail open without a second scorer call. Status and Desktop decision events now use schema v2 with decision type, allowed choices and cache behavior; the Desktop reader accepts both v1 and v2 during transition.

The native Anthropic path is limited to exact registered models on HTTPS `api.anthropic.com`. For the five per-message IDs, it merges Anthropic's dated beta into an already-exposed `anthropic-beta` header and replays effort markers before matching user turns while leaving top-level effort unchanged. The marker registry retains levels and hashed positions only. Without the header, `auto` pins per route and `always` uses top-level effort with a visible cache-reset caveat. Compression, missing anchors, manual initial-setting changes, reset/eviction and errors invalidate continuity. Local tests cover generated payloads; live API acceptance, cache effects, cost and response quality remain unmeasured.

The 2026-10-07 Desktop display fix corrects an identity mismatch: middleware's `agent.session_id` is a stored conversation ID, while the SDK's `focusedSessionId` is a temporary gateway runtime ID. The chip and REST lookup now use the stored ID, with owner-scoped `session.info` mappings for rotations. Native selector writes use the runtime ID and require an acknowledgment naming both IDs. Events add `conversation_id` and retain the historically mislabeled `runtime_session_id` alias for compatibility. This change needs the Desktop extension reloaded; already-running agent processes can keep sending the compatible older event shape.

Local verification for that fix: **546 Python tests and 17 Node Desktop scenarios passed**, including the reported `max → medium` case with unequal session IDs, plus Ruff and JavaScript syntax checks. The Node harness is now part of CI. Windows's agent-plugin junction resolves to this checkout, and the installed Desktop entry at `%LOCALAPPDATA%\hermes\desktop-plugins\hermes-adaptive-effort\plugin.js` automatically synchronized byte-for-byte with the updated source. These are local source/file checks; the next real chat turn's rendering remains to be confirmed interactively. No live scorer/generation call was made for this fix.

The pre-existing working tree contains a live model validation plan (`docs/plans/live-model-effort-validation.md`), runner/probe scripts, tests and campaign reports (`docs/validation/runs/live-20261007/report.md`). These files were not all tracked at the start of the refresh and are a separate workstream. The paths describe local work; this refresh does not publish them.

The 2026-10-07 report records a partially executed Codex campaign with deterministic local classifier scores; OpenRouter/Go generation cases were blocked before send. It also states transport/provenance limits and that calls were not repeated after the final probe hardening. Read the report before drawing conclusions. This is neither a live scorer evaluation nor exhaustive compatibility proof.

Preserve those files and their recorded boundaries. Review their tracking/publication status before distributing links to them; a local report may not yet exist on GitHub.

The Decisions and target-context plans describe implemented source paths. Their live scorer/quality/cost evaluations remain separate work, as recorded in their [design records](README.md#implemented-design-records).

The 2026-10-07 CLI history work adds a bounded per-conversation journal of applied effort changes and a matching focused-chat list in Desktop. It stores prompt-free metadata under the Hermes profile and clears one conversation on explicit reset. No live scorer or cache benchmark was run; the cache label is route evidence, not a measured savings claim.

## Operational follow-up

- Ox Alpha / `x-preview-f-free` is a retired route. Keep its earlier rejection observations in historical reports rather than current user-facing warnings. The operator reported on 2026-10-07 that it had been unavailable for at least a month; it is absent from the [current Zen model table](https://opencode.ai/docs/zen/#endpoints). The exact removal date was not independently established.
- Treat provider availability, scorer quality, cache effects and net savings as separate measurements.
- Distinguish route acceptance from reasoning-budget changes and answer quality.
- Validate host compatibility after Hermes updates because effort mapping and some integration paths use lazy internal imports.
- Follow the operator's backup policy for the archived recovery locations; this refresh did not inspect or clean them.

## Maintenance rule

Keep this file focused on source/publication evidence, dated deployments and outstanding operator work. Link to current guides for behavior. Preserve dated findings rather than updating old observations into present-tense claims.
