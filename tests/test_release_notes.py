"""test_release_notes: exercise the release helper script on temp copies."""

from __future__ import annotations

import json
import sys
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


# Import release_notes.py as a module
def _load_release_notes_module():
    script_path = Path(__file__).parent.parent / ".github" / "scripts" / "release_notes.py"
    spec = spec_from_file_location("release_notes", script_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_plugin_yaml_version(tmp_path, monkeypatch):
    """Test parsing version from plugin.yaml."""
    release_notes = _load_release_notes_module()

    # Create a temp plugin.yaml
    yaml_content = """name: test-plugin
version: 1.2.3
description: Test
"""
    yaml_path = tmp_path / "plugin.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")

    # Mock get_plugin_yaml_path
    monkeypatch.setattr(release_notes, "get_plugin_yaml_path", lambda: yaml_path)

    version = release_notes.parse_plugin_yaml_version()
    assert version == "1.2.3"


def test_parse_plugin_yaml_version_with_prerelease(tmp_path, monkeypatch):
    """Test parsing semver with prerelease suffix."""
    release_notes = _load_release_notes_module()

    yaml_content = """name: test-plugin
version: 1.2.3-rc.1
description: Test
"""
    yaml_path = tmp_path / "plugin.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")

    monkeypatch.setattr(release_notes, "get_plugin_yaml_path", lambda: yaml_path)

    version = release_notes.parse_plugin_yaml_version()
    assert version == "1.2.3-rc.1"


def test_cmd_version(tmp_path, monkeypatch):
    """Test version command."""
    release_notes = _load_release_notes_module()

    yaml_content = "version: 2.0.0\n"
    yaml_path = tmp_path / "plugin.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")

    monkeypatch.setattr(release_notes, "get_plugin_yaml_path", lambda: yaml_path)

    # Capture stdout
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        ret = release_notes.cmd_version()
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert ret == 0
    assert output.strip() == "2.0.0"


def test_cmd_notes(tmp_path, monkeypatch):
    """Test notes command."""
    release_notes = _load_release_notes_module()

    changelog_content = """# Changelog

## [Unreleased]

### Added
- New feature

## [1.0.0] - 2026-01-01

### Added
- First release

[Unreleased]: https://github.com/test/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/test/releases/tag/v1.0.0
"""
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(changelog_content, encoding="utf-8")

    monkeypatch.setattr(release_notes, "get_changelog_path", lambda: changelog_path)

    # Capture stdout
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        ret = release_notes.cmd_notes("1.0.0")
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert ret == 0
    assert "### Added" in output
    assert "First release" in output


def test_cmd_notes_missing_version(tmp_path, monkeypatch):
    """Test notes command when version is not found."""
    release_notes = _load_release_notes_module()

    changelog_content = "## [Unreleased]\n\n"
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(changelog_content, encoding="utf-8")

    monkeypatch.setattr(release_notes, "get_changelog_path", lambda: changelog_path)

    ret = release_notes.cmd_notes("99.0.0")
    assert ret != 0


def test_cmd_bump(tmp_path, monkeypatch):
    """Test bump command."""
    release_notes = _load_release_notes_module()

    # Setup temp files
    yaml_path = tmp_path / "plugin.yaml"
    yaml_path.write_text("version: 1.0.0\n", encoding="utf-8")

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"version": "1.0.0"}\n', encoding="utf-8")

    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_content = """# Changelog

## [Unreleased]

### Added
- New stuff

## [1.0.0] - 2026-01-01

### Added
- Initial release

[Unreleased]: https://github.com/test/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/test/releases/tag/v1.0.0
"""
    changelog_path.write_text(changelog_content, encoding="utf-8")

    monkeypatch.setattr(release_notes, "get_plugin_yaml_path", lambda: yaml_path)
    monkeypatch.setattr(release_notes, "get_manifest_path", lambda: manifest_path)
    monkeypatch.setattr(release_notes, "get_changelog_path", lambda: changelog_path)
    monkeypatch.setattr(release_notes, "get_repo_root", lambda: tmp_path)

    ret = release_notes.cmd_bump("2.0.0", today="2026-10-08")
    assert ret == 0

    # Verify updates
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "version: 2.0.0" in yaml_text

    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_data = json.loads(manifest_text)
    assert manifest_data["version"] == "2.0.0"

    changelog_text = changelog_path.read_text(encoding="utf-8")
    assert changelog_text == """# Changelog

## [Unreleased]

## [2.0.0] - 2026-10-08

### Added
- New stuff

## [1.0.0] - 2026-01-01

### Added
- Initial release

[Unreleased]: https://github.com/atostivint/hermes-adaptive-effort/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/atostivint/hermes-adaptive-effort/releases/tag/v2.0.0
[1.0.0]: https://github.com/test/releases/tag/v1.0.0
"""

    # The moved section is what the release workflow publishes.
    assert release_notes.cmd_bump("2.0.1", today="2026-10-09") == 1  # Unreleased now empty


def test_cmd_bump_invalid_version(tmp_path, monkeypatch):
    """Test bump with invalid semver."""
    release_notes = _load_release_notes_module()

    ret = release_notes.cmd_bump("not-a-version")
    assert ret != 0


def test_cmd_bump_empty_unreleased(tmp_path, monkeypatch):
    """Test bump when Unreleased section is empty."""
    release_notes = _load_release_notes_module()

    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_content = """# Changelog

## [Unreleased]

## [1.0.0]

- Old release

[Unreleased]: https://github.com/test/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/test/releases/tag/v1.0.0
"""
    changelog_path.write_text(changelog_content, encoding="utf-8")

    monkeypatch.setattr(release_notes, "get_changelog_path", lambda: changelog_path)

    ret = release_notes.cmd_bump("2.0.0")
    assert ret != 0
