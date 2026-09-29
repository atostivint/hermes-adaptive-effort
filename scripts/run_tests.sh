#!/usr/bin/env bash
# The exact invocation the suite is verified with: one interpreter, no network.
#
# Usage:  ./scripts/run_tests.sh [extra pytest args...]
#
# `tests/conftest.py` adds the Hermes source tree (agent/, hermes_cli/) to sys.path before
# the plugin is imported, so the real-dispatcher integration test runs here too. Point
# HERMES_SOURCE_ROOT at that tree when it does not live in the default location
# (/usr/local/lib/hermes-agent).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [ ! -x .venv/bin/python ]; then
  echo "no .venv in $root — run ./scripts/bootstrap_test_env.sh first" >&2
  exit 1
fi

exec ./.venv/bin/python -m pytest tests "$@"
