# Orchestration policy - hermes-adaptive-effort

The orchestrator reads this file at triage time. Workers only get what the brief quotes from it.

## What this repository is
`hermes-adaptive-effort` (directory `jev-auto-effort`) is a Hermes plugin that chooses the reasoning
effort of a request. Pure Python 3.10+, **no build step, no package**: the payload at the repo root is
installed as `~/.hermes/plugins/hermes-adaptive-effort`. Everything is verified by `pytest` and `ruff`
inside `.venv`.

## Hard rules (from AGENTS.md, repeated because they are not negotiable)
- Never commit, push, tag or open a PR on your own; the operator decides. `master` is protected.
- Never edit `~/.hermes/config.yaml`, install, restart or reconfigure a host (Iris, Desktop, gateway).
- Never add a root `pyproject.toml` or Python package metadata (Hermes treats it as a managed runtime).
- No network in tests. Every transport is injected (`transport=`, `key_reader=`, `_classifier_factory`).
- Never weaken a test, skip one, or rename a required CI job to get green.
- Never send prompt text, keys or personal paths into logs, reasons, traces, the feed or the history.
- Fail-open is the contract: a change that can raise on the request path is a bug, not a trade-off.
- Answer in French when the operator writes French; keep reports short and factual.

## Proof contract
A task is accepted only if its PROOF block contains:
1. `git diff --stat` (the files actually changed).
2. Every command run, with exit code and the relevant output. Minimum:
   - `.\scripts\run_tests.ps1 -q`  (network-free, must show passed with **no skips**)
   - `.\scripts\run_lint.ps1`      (ruff 0.16.9, must print `All checks passed!`)
3. Tests that ran, **by name**, for anything new (`-k` selector or node id).
4. For a contract/schema change: the exact field names and values touched, and the test file that pins them.
5. `gaps:` what could not be verified (live scorer call, Desktop UI, Windows-only path, other OS).

A summary without commands is rejected once. A second time, the task is closed as failed.

## No silent death (inherited from the template, kept because it happened there)
- A worker writes the CODE FIRST, in its first tool calls, then runs the commands, then reports. A
  report without a modified file is a failed task, however polite it is.
- Every brief ends with: "if you run out of steps, leave the tree in a green state and say exactly
  what is missing; never end with only a plan".
- The orchestrator checks the worktree (`git status --short`, `git diff --stat`) after EVERY worker
  returns, before reading its report. "It passes" is worth nothing without a diff.
- A worker that dies twice on the same task gets it split in two, not restarted.

## Scope rules
- The brief lists exact files. No file outside the list may be touched.
- Shared files, one active task at a time on each: `middleware.py`, `effort.py`, `command.py`,
  `__init__.py`, `plugin.yaml`, `dashboard/manifest.json`, `tests/conftest.py`, `CHANGELOG.md`.
- One worker per independent task. Parallel workers need disjoint files and separate worktrees
  (`git worktree add .worktrees/<name> -b <type>/<topic>`); never two workers on the same file.
- Before creating a task, run `git worktree list` and read `docs/HANDOFF.md`: the work may already
  exist on a `worktree-agent-*` branch. Never duplicate it.
- Only one full `run_tests.ps1` at a time per machine: the suite is short (about 25 s) but the venv
  and `%TEMP%` basetemp are shared.

## Routing
| The item is about | Domain |
|---|---|
| routing modes, decision cache, injection sites, escalation, registration, `/hae` command | `plugin-core` |
| scorer adapters, credentials, rubric, transport, retry, model profiles | `scorers` |
| cache safety, route trial verdicts, tool-error fingerprints | `route-trial` |
| dashboard backend/manifest, Desktop extension, icon | `ui-surfaces` |
| tests, CI, release scripts, version parity, docs, CHANGELOG | `tooling-docs` |

## Reply format (triage)
At most 8 lines:
```
#ID (one-line restatement) - related: #OTHER.
Domain: <domain> (session: <id or new>).
Covers: <what the change touches>.
Unverified: <live scorer / Desktop / other OS>.
```

## After each task
- Update `.orchestrator/registry.md` (row for the domain).
- Update the domain note's "Open threads" if it moved.
- Add one line to `.orchestrator/decisions.md` for any operator arbitration.
- The worker updates `CHANGELOG.md` under `## [Unreleased]` when the change is user-visible; the
  orchestrator only writes in `.orchestrator/` and `.opencode/domains/`.
