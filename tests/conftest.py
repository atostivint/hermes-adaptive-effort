"""Shared fixtures for Jev-Auto plugin tests.

Loads the plugin payload (``jev-auto/``) as a real package (the same sibling-module
layout Hermes' plugin loader builds) and guarantees unit tests never touch the network.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "jev-auto"
PLUGIN_PACKAGE = "hermes_plugin_jev_auto"

#: Where the Hermes *source tree* (``agent/``, ``hermes_cli/``) lives on this machine.
#: The plugin imports ``agent.reasoning_effort`` for effort clamping, and the dispatcher
#: integration test imports ``hermes_cli`` for the real middleware chain — but the test
#: venv is not the install venv. So the source root goes on ``sys.path`` once, here,
#: before any test imports plugin code. ``HERMES_SOURCE_ROOT`` overrides discovery, and
#: a candidate only counts when it really contains ``agent/reasoning_effort.py``.
_HERMES_SOURCE_CANDIDATES = tuple(
    value for value in (
        os.environ.get("HERMES_SOURCE_ROOT"),
        "/usr/local/lib/hermes-agent",
        str(Path.home() / ".hermes" / "hermes-agent"),
    ) if value
)


def ensure_hermes_source_on_path() -> str | None:
    """Make ``agent`` / ``hermes_cli`` importable for this test run.

    Returns the root that was added, ``""`` when they were importable already, or
    ``None`` when no Hermes source tree is installed. Absent core degrades exactly the
    way production code degrades: effort mapping refuses instead of guessing, and the
    dispatcher integration test skips itself.
    """
    try:
        import agent.reasoning_effort  # noqa: F401
        return ""
    except Exception:
        pass
    for candidate in _HERMES_SOURCE_CANDIDATES:
        root = Path(candidate).expanduser()
        if not (root / "agent" / "reasoning_effort.py").is_file():
            continue
        if str(root) in sys.path:
            return str(root)
        sys.path.insert(0, str(root))
        try:
            import agent.reasoning_effort  # noqa: F401
            return str(root)
        except Exception:
            sys.path.remove(str(root))
    return None


HERMES_SOURCE_ROOT = ensure_hermes_source_on_path()


def _load_plugin_package():
    if PLUGIN_PACKAGE in sys.modules:
        return sys.modules[PLUGIN_PACKAGE]
    spec = importlib.machinery.ModuleSpec(PLUGIN_PACKAGE, None, is_package=True)
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[PLUGIN_PACKAGE] = pkg
    return pkg


@pytest.fixture(scope="session")
def plugin_pkg():
    return _load_plugin_package()


def import_plugin(stem: str):
    """Import ``<plugin>/<stem>.py`` as ``hermes_plugin_jev_auto.<stem>``."""
    full_name = f"{PLUGIN_PACKAGE}.{stem}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    _load_plugin_package()
    path = PLUGIN_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(full_name, str(path))
    assert spec and spec.loader, f"cannot build spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _NoNetwork:
    """Raise on any attempt to open a socket; records nothing else."""

    def __call__(self, *args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("unit tests must not open network sockets")


@pytest.fixture
def no_network(monkeypatch):
    """Fail the test if anything opens a socket while this fixture is active."""
    import socket

    guard = _NoNetwork()
    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    return guard
