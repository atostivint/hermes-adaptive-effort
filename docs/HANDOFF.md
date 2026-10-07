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

The pre-existing working tree contains a live model validation plan (`docs/plans/live-model-effort-validation.md`), runner/probe scripts, tests and campaign reports (`docs/validation/runs/live-20261007/report.md`). These files were not all tracked at the start of the refresh and are a separate workstream. The paths describe local work; this refresh does not publish them.

The 2026-10-07 report records a partially executed Codex campaign with deterministic local classifier scores; OpenRouter/Go generation cases were blocked before send. It also states transport/provenance limits and that calls were not repeated after the final probe hardening. Read the report before drawing conclusions. This is neither a live scorer evaluation nor exhaustive compatibility proof.

Preserve those files and their recorded boundaries. Review their tracking/publication status before distributing links to them; a local report may not yet exist on GitHub.

The Decisions and target-context plans describe implemented source paths. Their live scorer/quality/cost evaluations remain separate work, as recorded in their [design records](README.md#implemented-design-records).

## Operational follow-up

- Ox Alpha / `x-preview-f-free` is a retired route. Keep its earlier rejection observations in historical reports rather than current user-facing warnings. The operator reported on 2026-10-07 that it had been unavailable for at least a month; it is absent from the [current Zen model table](https://opencode.ai/docs/zen/#endpoints). The exact removal date was not independently established.
- Treat provider availability, scorer quality, cache effects and net savings as separate measurements.
- Distinguish route acceptance from reasoning-budget changes and answer quality.
- Validate host compatibility after Hermes updates because effort mapping and some integration paths use lazy internal imports.
- Follow the operator's backup policy for the archived recovery locations; this refresh did not inspect or clean them.

## Maintenance rule

Keep this file focused on source/publication evidence, dated deployments and outstanding operator work. Link to current guides for behavior. Preserve dated findings rather than updating old observations into present-tense claims.
