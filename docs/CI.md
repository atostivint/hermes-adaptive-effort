# Automated quality and security checks

The public repository uses standard GitHub-hosted runners. No scorer credentials are required or exposed to workflows, and tests block socket creation. Dependency/tool downloads and vulnerability database queries use the network before or outside the test run.

## Functional checks (`CI`)

Every push, pull request and manual dispatch runs:

- The complete pytest suite on Ubuntu 24.04 with Python 3.11, 3.12 and 3.14, and Windows 2025 with Python 3.12.
- Real Hermes discovery and middleware-dispatch integration using a separately checked-out host, not a stub host.
- A JUnit report guard that rejects failures, skips, an empty suite or fewer than six dispatcher integration tests. A missing Hermes install cannot make CI green by skipping integration.
- Ruff through the project's standard lint script, plus the hidden CI helper directory.
- Node syntax validation of the optional Desktop extension.
- Actionlint validation of workflow expressions, action inputs and shell commands.

Hermes is pinned to the verified public source revision `0a374d167424cdc730ce9761368b62255b551e58`. CI installs only this project's declared development dependencies, imports the host modules directly and does not install the plugin as a Python package. Test processes use an isolated `HERMES_HOME` and no operator configuration.

JUnit reports are retained as artifacts for seven days. Runtime behavior tests cover turn reuse, field preservation, provider mapping, opt-in modes, subagent gating, privacy, settings/manifest parity and failure handling. Desktop behavior tests include static contracts; syntax checking does not replace an interactive UI test.

## Security checks (`Security`)

Pushes to master and `codex/**`, pull requests, a weekly schedule and manual dispatch run:

- `pip-audit` against declared development and security-tool requirements, including resolved dependencies. Known advisory findings fail the job. The plugin itself has no pip runtime distribution or third-party direct runtime dependencies; its Hermes host has a separate dependency surface outside this audit.
- Gitleaks against the full git history, with output fully redacted. Findings fail the job. Do not add exceptions without reviewing the actual finding privately.
- CodeQL `security-extended` analysis for Python and JavaScript/TypeScript. Results are uploaded to GitHub code scanning. A successful analysis job means analysis completed, not that no alerts exist; review the repository's Security tab for findings.

Dependabot proposes weekly updates for GitHub Actions and Python requirements. Updates are reviewed and tested rather than automatically merged. The manually pinned Hermes source revision is not updated by Dependabot.

GitHub secret scanning and push protection complement the history scan. These repository settings depend on GitHub account permissions and should be checked in Settings → Code security.

## Workflow permissions and cost

Checkout and setup actions are pinned to immutable commit hashes. Workflows use `pull_request`, not `pull_request_target`, do not deploy, and do not receive scorer keys. Checkout does not persist Git credentials. The default token permission is `contents: read`; only CodeQL receives `security-events: write` and the read permissions needed for analysis. Jobs have timeouts, and newer runs cancel older runs for the same ref.

Standard hosted runner compute is free for public repositories; larger runners are billed. Artifact/cache storage has separate allowances. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions). The workflows use standard Ubuntu/Windows labels, no model API calls, no cache, and short artifact retention.

## Working with failures

Open the failing Actions run and inspect its named job. Test failures include a JUnit artifact. A host import failure is a compatibility/dependency problem, not a reason to disable integration tests. Security findings require a patch or a documented, narrow false-positive determination; do not suppress the whole check to get a green run.

These checks make regressions and known security issues visible. They do not certify model-answer quality, live scorer availability, vendor effort acceptance or cost/cache savings. The known Ox Alpha limitation and live provider evaluations remain documented separately.

## First-run finding

The initial hosted test matrix passed, while CodeQL reported `py/stack-trace-exposure` in the mode API. A failed persistent mode write previously returned raw exception class/text. The API now returns only `persist_failed`, preserving the failure status and the existing process-local mode change. A regression test injects sensitive exception text and verifies it is absent from the public result.
