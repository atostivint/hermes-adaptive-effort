#!/usr/bin/env bash
# Recreate the environment the suite is verified with, then run the suite in it.
#
# Usage:  ./scripts/bootstrap_test_env.sh [extra pytest args...]
# Needs python3 and network access (pip); nothing is installed outside this checkout.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# --system-site-packages is not decoration: the integration test imports the installed
# Hermes source tree (hermes_cli.plugins, hermes_cli.config, ...), whose own third-party
# imports (rich, httpx, ruamel, ...) come from the interpreter that ships them.
python3 -m venv --system-site-packages .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements-dev.txt

exec ./scripts/run_tests.sh "$@"
