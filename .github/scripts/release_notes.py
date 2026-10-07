#!/usr/bin/env python3
"""Release notes helper: version, notes, bump.

Usage:
  python release_notes.py version
  python release_notes.py notes <version>
  python release_notes.py bump <version>
"""

import sys
import re
import json
import io
from datetime import date
from pathlib import Path

# Ensure UTF-8 output on all platforms
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def get_repo_root():
    """Return the repo root (parent of .github/scripts/)."""
    return Path(__file__).parent.parent.parent


def get_plugin_yaml_path():
    return get_repo_root() / "plugin.yaml"


def get_manifest_path():
    return get_repo_root() / "dashboard" / "manifest.json"


def get_changelog_path():
    return get_repo_root() / "CHANGELOG.md"


def parse_plugin_yaml_version():
    """Extract version from plugin.yaml using regex (no YAML parser)."""
    path = get_plugin_yaml_path()
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([^\s]+)$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not parse version from {path}")
    return match.group(1).strip()


def cmd_version():
    """Print plugin.yaml version."""
    version = parse_plugin_yaml_version()
    print(version)
    return 0


def cmd_notes(version):
    """Print CHANGELOG.md section for the given version (excluding heading)."""
    changelog_path = get_changelog_path()
    if not changelog_path.exists():
        print(f"Error: {changelog_path} not found", file=sys.stderr)
        return 1

    text = changelog_path.read_text(encoding="utf-8")

    # Find the heading for this version
    heading = f"## [{version}]"
    start_idx = text.find(heading)
    if start_idx == -1:
        print(f"Error: No section found for version [{version}]", file=sys.stderr)
        return 1

    # Skip the heading line
    start_idx = text.find("\n", start_idx) + 1

    # Find the next heading or link-reference block
    next_heading = text.find("\n## [", start_idx)
    next_links = text.find("\n[", start_idx)

    # Determine where the section ends
    end_idx = len(text)
    if next_heading != -1:
        end_idx = next_heading
    if next_links != -1 and next_links < end_idx:
        end_idx = next_links

    section = text[start_idx:end_idx].rstrip()

    # Check if section is empty
    if not section.strip():
        print(f"Error: Section for version [{version}] is empty", file=sys.stderr)
        return 1

    print(section, end="")

    # Append link to full release notes if they exist
    release_notes_path = get_repo_root() / f"docs/releases/v{version}.md"
    if release_notes_path.exists():
        print("\n\nFull release notes: https://github.com/atostivint/hermes-adaptive-effort/blob/v"
              f"{version}/docs/releases/v{version}.md")
    else:
        print()

    return 0


def cmd_bump(version, today=None):
    """Validate semver, update plugin.yaml, manifest.json and CHANGELOG.md."""
    # Validate semver
    if not re.match(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$", version):
        print(f"Error: '{version}' is not valid semver", file=sys.stderr)
        return 1

    plugin_yaml_path = get_plugin_yaml_path()
    manifest_path = get_manifest_path()
    changelog_path = get_changelog_path()

    # Check Unreleased section is not empty
    changelog_text = changelog_path.read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog_text:
        print(f"Error: No '## [Unreleased]' heading in {changelog_path}", file=sys.stderr)
        return 1

    # Extract Unreleased content (from ## [Unreleased] to next ## [)
    unreleased_start = changelog_text.find("## [Unreleased]")
    unreleased_content_start = changelog_text.find("\n", unreleased_start) + 1
    next_section = changelog_text.find("\n## [", unreleased_content_start)
    if next_section == -1:
        next_section = len(changelog_text)
    unreleased_content = changelog_text[unreleased_content_start:next_section].rstrip()

    if not unreleased_content.strip():
        print(f"Error: Unreleased section in {changelog_path} is empty", file=sys.stderr)
        return 1

    # Check if version already exists
    if f"## [{version}]" in changelog_text:
        print(f"Error: Version [{version}] already exists in {changelog_path}", file=sys.stderr)
        return 1

    # Update files
    plugin_yaml_text = plugin_yaml_path.read_text(encoding="utf-8")
    plugin_yaml_text = re.sub(
        r"^version:\s*[^\s]+$",
        f"version: {version}",
        plugin_yaml_text,
        count=1,
        flags=re.MULTILINE
    )
    plugin_yaml_path.write_text(plugin_yaml_text, encoding="utf-8")

    # Update dashboard/manifest.json
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_data = json.loads(manifest_text)
    manifest_data["version"] = version
    # Write in one-line format with sorted keys
    new_manifest_text = json.dumps(manifest_data, sort_keys=True)
    # Preserve trailing newline if original had it
    if manifest_text.endswith("\n"):
        new_manifest_text += "\n"
    manifest_path.write_text(new_manifest_text, encoding="utf-8")

    # Update CHANGELOG.md: move the Unreleased body under a new dated heading
    # and leave an empty Unreleased section above it.
    today = today or date.today().isoformat()
    nl = "\n"
    changelog_new_text = (
        changelog_text[:unreleased_start]
        + "## [Unreleased]" + nl + nl
        + f"## [{version}] - {today}" + nl + nl
        + unreleased_content.strip(nl)
        + nl
        + changelog_text[next_section:]
    )

    # Update link references: Unreleased compares from the new tag, and the
    # new version gets its own tag link.
    repo_url = "https://github.com/atostivint/hermes-adaptive-effort"
    new_links = (
        f"[Unreleased]: {repo_url}/compare/v{version}...HEAD" + nl
        + f"[{version}]: {repo_url}/releases/tag/v{version}"
    )
    changelog_new_text, replaced = re.subn(
        r"^\[Unreleased\]:.*$",
        lambda _match: new_links,
        changelog_new_text,
        count=1,
        flags=re.MULTILINE,
    )
    if not replaced:
        changelog_new_text = changelog_new_text.rstrip(nl) + nl + nl + new_links + nl

    changelog_path.write_text(changelog_new_text, encoding="utf-8")

    print(f"Bumped version to {version}")
    return 0


def usage():
    print("Usage:")
    print("  python release_notes.py version")
    print("  python release_notes.py notes <version>")
    print("  python release_notes.py bump <version>")
    return 2


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(usage())

    cmd = sys.argv[1]

    if cmd == "version":
        sys.exit(cmd_version())
    elif cmd == "notes":
        if len(sys.argv) < 3:
            sys.exit(usage())
        sys.exit(cmd_notes(sys.argv[2]))
    elif cmd == "bump":
        if len(sys.argv) < 3:
            sys.exit(usage())
        sys.exit(cmd_bump(sys.argv[2]))
    else:
        sys.exit(usage())
