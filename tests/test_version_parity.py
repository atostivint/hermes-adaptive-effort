"""version_parity: plugin.yaml, dashboard/manifest.json and CHANGELOG.md are in sync."""

from __future__ import annotations

import json
import re
from conftest import PLUGIN_DIR


def _load_plugin_yaml_version() -> str:
    text = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    from ruamel.yaml import YAML
    data = YAML(typ="safe").load(text)
    assert isinstance(data, dict), "plugin.yaml must parse as a YAML mapping"
    version = data.get("version")
    assert isinstance(version, str), "plugin.yaml must have a version string"
    return version


def _load_manifest_version() -> str:
    manifest_path = PLUGIN_DIR / "dashboard" / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    data = json.loads(text)
    version = data.get("version")
    assert isinstance(version, str), "dashboard/manifest.json must have a version string"
    return version


def _load_changelog() -> str:
    changelog_path = PLUGIN_DIR / "CHANGELOG.md"
    return changelog_path.read_text(encoding="utf-8")


def test_plugin_yaml_version_matches_semver():
    """Parse plugin.yaml version and validate strict semver with optional prerelease."""
    version = _load_plugin_yaml_version()
    # Strict semver: X.Y.Z optionally with -prerelease suffix
    pattern = r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$"
    assert re.match(pattern, version), (
        f"plugin.yaml version '{version}' does not match semver pattern {pattern}"
    )


def test_manifest_version_equals_plugin_yaml():
    """Assert dashboard/manifest.json version equals plugin.yaml version."""
    plugin_version = _load_plugin_yaml_version()
    manifest_version = _load_manifest_version()
    assert manifest_version == plugin_version, (
        f"manifest.json version '{manifest_version}' != plugin.yaml version '{plugin_version}'"
    )


def test_changelog_has_unreleased_and_current_version():
    """Assert CHANGELOG.md has Unreleased heading and current version heading."""
    changelog = _load_changelog()
    version = _load_plugin_yaml_version()

    # Check for Unreleased section
    assert "## [Unreleased]" in changelog, (
        "CHANGELOG.md must have a '## [Unreleased]' heading"
    )

    # Check for current version section
    version_heading = f"## [{version}]"
    assert version_heading in changelog, (
        f"CHANGELOG.md must have a '{version_heading}' heading for the current version"
    )
