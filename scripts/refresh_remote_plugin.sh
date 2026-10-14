#!/usr/bin/env bash
# Refresh the plugin installed on a remote Hermes host from a committed revision of this checkout.
#
# Usage:  HAE_REMOTE_HOST=<ssh destination> ./scripts/refresh_remote_plugin.sh [--dry-run] [REV]
#
# Ships `git archive REV` (default HEAD) over SSH — never uncommitted edits — swaps it into
# the Hermes plugin directory, restarts the gateway and rolls back if the unit does not come
# back up. The installed checkout's .git is carried over, the previous install is kept under
# the host's scratch cache, and a regular-file Desktop plugin.js is replaced alongside it.
# Record each live refresh as a dated entry in docs/HANDOFF.md.
#
#   HAE_REMOTE_HOST     SSH destination, e.g. an ~/.ssh/config alias (required unless --dry-run)
#   HAE_REMOTE_HOME     Hermes home on the host (default: /root/.hermes)
#   HAE_REMOTE_SERVICE  gateway unit in the user systemd manager (default: hermes-gateway.service)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

host="${HAE_REMOTE_HOST:-}"
hermes_home="${HAE_REMOTE_HOME:-/root/.hermes}"
service="${HAE_REMOTE_SERVICE:-hermes-gateway.service}"

dry_run=0
rev=HEAD
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) rev="$arg" ;;
  esac
done

if [ -z "$host" ] && [ "$dry_run" -eq 0 ]; then
  echo "set HAE_REMOTE_HOST to the SSH destination" >&2
  exit 2
fi

commit="$(git rev-parse --verify --quiet "${rev}^{commit}")" || {
  echo "not a commit: $rev" >&2
  exit 2
}
short="$(git rev-parse --short "$commit")"
if ! git cat-file -e "$commit:plugin.yaml" 2>/dev/null; then
  echo "$short has no plugin.yaml at the repository root" >&2
  exit 1
fi
version="$(git show "$commit:plugin.yaml" | sed -n 's/^version:[[:space:]]*//p' | head -n 1)"
if [ -n "$(git status --porcelain)" ]; then
  echo "note: the working tree has uncommitted changes; only $short is shipped" >&2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git archive --format=tar -o "$work/payload.tar" "$commit"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage="$hermes_home/cache/scratch/hae-refresh-$stamp"

echo "revision: $short (plugin version ${version:-unknown})"
echo "host:     ${host:-<unset>}"
echo "target:   $hermes_home/plugins/hermes-adaptive-effort"
echo "backup:   $stage/previous"
echo "restart:  systemctl --user restart $service"
if [ "$dry_run" -eq 1 ]; then
  echo "dry run: $(tar -tf "$work/payload.tar" | grep -cv '/$') files packed; nothing sent"
  exit 0
fi

ssh "$host" "mkdir -p '$stage' && cat > '$stage/payload.tar'" < "$work/payload.tar"

ssh "$host" bash -s -- "$hermes_home" "$service" "$stage" "$short" <<'REMOTE'
set -euo pipefail
hermes_home="$1" service="$2" stage="$3" short="$4"
target="$hermes_home/plugins/hermes-adaptive-effort"
desktop="$hermes_home/desktop-plugins/hermes-adaptive-effort/plugin.js"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

mkdir "$stage/new"
tar -xf "$stage/payload.tar" -C "$stage/new"
test -f "$stage/new/plugin.yaml"
if [ -d "$target/.git" ]; then
  cp -a "$target/.git" "$stage/new/.git"
fi

if [ -e "$target" ]; then
  mv "$target" "$stage/previous"
fi
mv "$stage/new" "$target"
if [ -f "$desktop" ] && [ ! -L "$desktop" ]; then
  cp -p "$desktop" "$stage/plugin.js.previous"
  cp "$target/desktop/plugin.js" "$desktop"
fi

restart() {
  systemctl --user restart "$service"
  sleep 5
  systemctl --user is-active --quiet "$service"
}

if ! restart; then
  echo "$service is not active after the refresh; rolling back" >&2
  mv "$target" "$stage/failed"
  if [ -e "$stage/previous" ]; then
    mv "$stage/previous" "$target"
  fi
  if [ -f "$stage/plugin.js.previous" ]; then
    cp -p "$stage/plugin.js.previous" "$desktop"
  fi
  restart || echo "$service is still not active after rollback" >&2
  exit 1
fi

rm -f "$stage/payload.tar"
echo "installed $short; $service is active"
echo "plugin.yaml sha256: $(sha256sum "$target/plugin.yaml" | cut -d' ' -f1)"
echo "previous install:   $stage/previous"
REMOTE
