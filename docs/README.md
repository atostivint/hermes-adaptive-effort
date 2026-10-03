# Documentation

Start with the [project README](../README.md) for installation, configuration and everyday use.

## Current references

| Document | Purpose |
| --- | --- |
| [Design choices](DESIGN.md) | Maintainer intent and the reasons behind the scope and safety rules |
| [Runtime contracts](CONTRACTS.md) | Decision scope, wire mapping, failure behavior, privacy and APIs |
| [Automated checks](CI.md) | GitHub Actions test matrix, integration gate, security scans and their limits |
| [Development](DEVELOPMENT.md) | Layout, test contracts and Windows/Linux verification |
| [Contributor instructions](../AGENTS.md) | Rules to follow when changing the implementation |
| [Operator handoff](HANDOFF.md) | Dated deployment facts for Iris and Windows, migration and unresolved operational items |

Code is the reference for current behavior. The handoff records an operator environment at a point in time; it is not an installation prerequisite for other users.

## Future integrations

| Document | Status |
| --- | --- |
| [OpenAI Decision API plan](plans/openai-decision-api.md) | Deferred until release; API contract and settings are provisional |

## Historical reviews

These reports preserve the findings and identifiers from their original dates. Old names, paths, test counts and deployment states are historical, not current instructions.

| Document | Context |
| --- | --- |
| [Original delivery handoff](handoff-t_cb5d47d0.md) | Card `t_cb5d47d0`, prior identity and initial acceptance evidence |
| [DeepSeek review, 2026-09-29](review-deepseek-2026-09-29.md) | Read-only review of the then-installed host/plugin; several findings subsequently fixed |
| [Review follow-up, 2026-09-29](review-followup-2026-09-29.md) | Fixes and installation state at that date |

New documentation should distinguish tested contracts, dated live observations and unmeasured expectations. Do not rewrite old reports to make an earlier result look current.
