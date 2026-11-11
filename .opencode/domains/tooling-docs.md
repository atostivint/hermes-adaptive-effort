# Domain: tooling-docs

Everything that is not runtime code: test harness, CI, release, documentation and changelog.

## Owns
- `tests/` - one module per contract, `conftest.py` (plugin import, `no_network`, `hermetic_plugin_settings`),
  `fixtures/`, `test_dispatcher_integration.py` (real PluginManager discovery, never skipped).
- `scripts/run_tests.ps1|.sh`, `run_lint.ps1|.sh`, `bootstrap_test_env.ps1|.sh`,
  `release.ps1|.sh`, the replay/eval/benchmark helpers.
- `.github/workflows/`, `.github/scripts/`, `pull_request_template.md`, issue templates, dependabot.
- `pytest.ini`, `ruff.toml`, `requirements-dev.txt`, `requirements-security.txt`.
- `README.md`, `CHANGELOG.md`, `docs/` (CONTRACTS, DEVELOPMENT, HANDOFF, DESIGN, USAGE, CI, plans, reviews).

## Commands (the only ones that count as proof)
- `.\scripts\run_tests.ps1 -q` - network-free, ~25 s, must pass with **no skips**. If it warns that no
  Hermes source tree was found, set `HERMES_SOURCE_ROOT` to a checkout containing
  `agent/reasoning_effort.py`; never accept the resulting failures as pre-existing.
- `.\scripts\run_lint.ps1` - ruff 0.16.9, `select = ["E","F","W","B"]`, `E501` ignored, import sorting off.
- `.\scripts\bootstrap_test_env.ps1` - fresh machine: `.venv` + dev requirements + first run.
- Workflows changed: `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12`
  (CI also runs shellcheck through actionlint, so quote every shell variable).

## Invariants
- Never weaken, skip or quarantine a test to get green; fix the cause.
- `pyproject.toml` stays absent: Hermes treats any root `pyproject.toml` as a managed runtime member.
- `CHANGELOG.md`: one line under `## [Unreleased]` per user-visible change. Never edit a released
  section or bump a version by hand.
- Required checks are the four `Tests (...)` legs, `Lint and workflow validation`,
  `Dependency and secret checks`, and both `CodeQL (...)` jobs. Never rename one away.
- Gitleaks scans the full history: no secrets, keys, personal paths or host addresses in a commit.
- Deploying to Iris/Desktop is an operator step; record any host change in `docs/HANDOFF.md`.

## Known traps
- `%TEMP%\pytest-of-<user>` may carry a foreign ACL: `run_tests.ps1` then falls back to a scratch
  `--basetemp` and prints a warning. That warning is not a failure.
- `master` is protected by a ruleset: a direct push is rejected, work lands through a squash-merged PR.
- Release is two-step (`prepare X.Y.Z`, then `tag X.Y.Z`) and operator-triggered; never move tags by hand.

## Proof
- The two commands above with their exit codes and the passing count.
- For a workflow change: the actionlint run and what it printed.

## Open threads
- `docs/` carries dated reviews and validation runs; they are history, not current truth. Check the code.
