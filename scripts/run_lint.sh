#!/usr/bin/env bash
# Lint the payload and the tests with the pinned ruff (rules live in ruff.toml).
#
# Usage:  ./scripts/run_lint.sh [extra ruff args...]
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [ ! -x .venv/bin/ruff ]; then
  echo "no .venv/bin/ruff in $root — run ./scripts/bootstrap_test_env.sh first" >&2
  exit 1
fi

exec ./.venv/bin/ruff check . "$@"
