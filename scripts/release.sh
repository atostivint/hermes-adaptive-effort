#!/usr/bin/env bash
# Release helper: prepare a release branch or tag a release.
#
# Usage:
#   ./scripts/release.sh prepare X.Y.Z
#   ./scripts/release.sh tag X.Y.Z
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# Ensure gh is available
if ! command -v gh &> /dev/null; then
  echo "Error: gh (GitHub CLI) is not installed" >&2
  exit 1
fi

# Determine Python to use
if [ -x .venv/bin/python ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

if ! command -v "$PYTHON" &> /dev/null; then
  echo "Error: Python not found" >&2
  exit 1
fi

usage() {
  echo "Usage:"
  echo "  ./scripts/release.sh prepare X.Y.Z"
  echo "  ./scripts/release.sh tag X.Y.Z"
  exit 2
}

prepare_release() {
  local version="$1"

  # Check clean working tree
  if [ -n "$(git status --porcelain)" ]; then
    echo "Error: Working tree is not clean" >&2
    git status --short >&2
    exit 1
  fi

  # Fetch origin
  git fetch origin

  # Create release branch
  local branch="release/v$version"
  echo "Creating branch $branch from origin/master..."
  git checkout -b "$branch" "origin/master"

  # Run the bump script
  echo "Bumping version to $version..."
  "$PYTHON" .github/scripts/release_notes.py bump "$version"

  # Run tests
  echo "Running tests..."
  ./scripts/run_tests.sh --tb=short

  # Commit
  local commit_msg="release: v$version"
  git add -A
  git commit -m "$commit_msg"

  # Push branch
  echo "Pushing branch $branch..."
  git push -u origin "$branch"

  # Get release notes
  echo "Creating pull request..."
  local notes_file=$(mktemp)
  "$PYTHON" .github/scripts/release_notes.py notes "$version" > "$notes_file" 2>&1

  # Open PR
  gh pr create --base master --title "release: v$version" --body-file "$notes_file"

  rm -f "$notes_file"

  echo ""
  echo "After merge: $0 tag $version"
}

tag_release() {
  local version="$1"

  # Tag origin/master directly, so this works from any worktree or branch.
  git fetch origin master --tags
  local target
  target=$(git rev-parse origin/master)

  # Verify the version on origin/master matches
  local plugin_version
  plugin_version=$(git show "$target:plugin.yaml" | sed -n 's/^version:[[:space:]]*//p' | head -n 1 | tr -d '[:space:]')
  if [ "$plugin_version" != "$version" ]; then
    echo "Error: plugin.yaml on origin/master is '$plugin_version', not '$version'" >&2
    exit 1
  fi

  # Check tag doesn't exist
  if git rev-parse "v$version" > /dev/null 2>&1; then
    echo "Error: Tag v$version already exists locally" >&2
    exit 1
  fi

  if git ls-remote --tags origin "v$version" | grep -q "refs/tags/v$version"; then
    echo "Error: Tag v$version already exists on origin" >&2
    exit 1
  fi

  # Create and push tag
  local tag_msg="Hermes Adaptive Effort v$version"
  git tag -a "v$version" -m "$tag_msg" "$target"
  git push origin "v$version"

  echo ""
  echo "Tag v$version pushed. Release workflow will publish it."
}

if [ $# -lt 2 ]; then
  usage
fi

cmd="$1"
version="$2"

case "$cmd" in
  prepare)
    prepare_release "$version"
    ;;
  tag)
    tag_release "$version"
    ;;
  *)
    usage
    ;;
esac
