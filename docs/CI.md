# Automated quality and security checks

[Development and local commands](DEVELOPMENT.md) · [Runtime contracts](CONTRACTS.md)

The public repository uses standard GitHub-hosted runners. No scorer credentials are required or exposed to workflows, and tests block socket creation. Dependency/tool downloads and vulnerability database queries use the network before or outside the test run.

## Functional checks (`CI`)

Every push, pull request and manual dispatch runs:

- The complete pytest suite on Ubuntu 24.04 with Python 3.11, 3.12 and 3.14, and Windows 2025 with Python 3.12.
- Real Hermes discovery and middleware-dispatch integration using a separately checked-out host, not a stub host.
- A JUnit report guard that rejects failures, skips, an empty suite or fewer than six dispatcher integration tests. A missing Hermes install cannot make CI green by skipping integration.
- Ruff through the project's standard lint script, plus the hidden CI helper directory.
- Node syntax validation and the behavior harness for the optional Desktop extension.
- Actionlint validation of workflow expressions, action inputs and shell commands.

Hermes is pinned to the verified public source revision `0a374d167424cdc730ce9761368b62255b551e58`. CI installs only this project's declared development dependencies, imports the host modules directly and does not install the plugin as a Python package. Test processes use an isolated `HERMES_HOME` and no operator configuration.

JUnit reports are retained as artifacts for seven days. Runtime behavior tests cover turn reuse, field preservation, provider mapping, opt-in modes, subagent gating, privacy, settings/manifest parity and failure handling. The pytest suite includes Desktop static contracts. CI also runs the Node behavior harness documented in Development, including distinct stored/runtime session IDs, focused-chat rendering and selector acknowledgment. Neither syntax nor mocked behavior tests replace an interactive UI check.

## Security checks (`Security`)

Pushes to master and `codex/**`, pull requests, a weekly schedule and manual dispatch run:

- `pip-audit` against declared development and security-tool requirements, including resolved dependencies. Known advisory findings fail the job. The plugin itself has no pip runtime distribution or third-party direct runtime dependencies; its Hermes host has a separate dependency surface outside this audit.
- Gitleaks against the full git history, with output fully redacted. Findings fail the job. Do not add exceptions without reviewing the actual finding privately.
- CodeQL `security-extended` analysis for Python and JavaScript/TypeScript. Results are uploaded to GitHub code scanning. A successful analysis job means analysis completed, not that no alerts exist; review the repository's Security tab for findings.

Dependabot proposes weekly updates for GitHub Actions and Python requirements. Updates are reviewed and tested rather than automatically merged. The manually pinned Hermes source revision is not updated by Dependabot.

GitHub secret scanning and push protection complement the history scan. These repository settings depend on GitHub account permissions and should be checked in Settings → Code security.

## Release workflow (`Release`)

Triggered by pushing a `vX.Y.Z` tag:

- Verifies the tag version matches plugin.yaml
- Verifies the tag commit is on origin/master
- Generates release notes from CHANGELOG.md
- Creates a GitHub release with `--prerelease` when major version is 0 or a prerelease suffix is present

No deployment occurs; release creation is a hosted operation only. The `contents: write` permission is held by this job only.

## Coverage artifact

CI collects coverage.xml on the ubuntu-24.04 Python 3.12 leg (informational, no threshold). The artifact is retained for 7 days for download and analysis.

## Hermes canary (`hermes-canary`)

Runs weekly (Monday 06:41 UTC) and on manual dispatch against Hermes master to detect compatibility issues. Non-blocking status check; the plugin should continue to work with the latest Hermes even between pinned-revision CI updates.

## Workflow permissions and cost

Checkout and setup actions are pinned to immutable commit hashes. Workflows use `pull_request`, not `pull_request_target`, and do not receive scorer keys. Checkout does not persist Git credentials. The default token permission is `contents: read`. The Release job gets `contents: write`; CodeQL receives `security-events: write` and the read permissions needed for analysis. No workflow deploys. Jobs have timeouts, and newer runs cancel older runs for the same ref.

The workflows use standard Ubuntu/Windows labels, no model API calls, no cache and short artifact retention. Check [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) for the current compute and storage policy.

## Working with failures

Open the failing Actions run and inspect its named job. Test failures include a JUnit artifact. A host import failure is a compatibility/dependency problem, not a reason to disable integration tests. Security findings require a patch or a documented, narrow false-positive determination; do not suppress the whole check to get a green run.

These checks make regressions and known security issues visible. They do not certify model-answer quality, live scorer availability, vendor effort acceptance or cost/cache savings. See [Compatibility](MODEL_COMPATIBILITY.md) for route evidence and [dated reports](README.md#historical-evidence) for past provider observations.

## Historical first-run finding

The initial hosted test matrix passed, while CodeQL reported `py/stack-trace-exposure` in the mode API. A failed persistent mode write previously returned raw exception class/text. The API now returns only `persist_failed`, preserving the failure status and the existing process-local mode change. A regression test injects sensitive exception text and verifies it is absent from the public result.
