"""Shared fixtures for Hermes Adaptive Effort plugin tests.

Loads the repository-root plugin payload as a real package (the same sibling-module layout
Hermes' plugin loader builds) and guarantees unit tests never touch the network.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGIN_PACKAGE = "hermes_plugin_adaptive_effort"

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
    """Import ``<plugin>/<stem>.py`` as ``hermes_plugin_adaptive_effort.<stem>``."""
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


@pytest.fixture(scope="session", autouse=True)
def no_network():
    """Fail the whole run if ANY test opens a socket.

    Session-scoped and autouse on purpose: an opt-in, function-scoped guard
    proves nothing about the tests that forgot to request it, and it would not
    cover module-scoped fixtures either. Patching for the entire session covers
    both, so "the suite provably opens no socket" is a property of the run
    rather than of the tests that remembered.
    """
    import socket
    from unittest import mock

    guard = _NoNetwork()
    with mock.patch.object(socket, "socket", guard), \
            mock.patch.object(socket, "create_connection", guard):
        yield guard


@pytest.fixture(autouse=True)
def hermetic_plugin_settings(monkeypatch):
    """Every test starts from the documented defaults, never from the live profile.

    Two seams are involved, and both matter once the Hermes core is importable:

    * ``_settings_provider`` — tests that set it to ``None`` deliberately drop
      back to the config reader, which used to read
      ``~/.hermes/config.yaml``. The live profile must not affect default-mode
      assertions or cause an unexpected scorer call.
    * ``_config_reader`` — the injected reader below is that same fallback, made
      hermetic: ``{}`` means "no settings anywhere", i.e. every documented
      default (including the default ``off`` mode). It is ``None`` in production,
      so the live reader is unchanged.

    It also resets in-memory state before and after each test, so no decision and
    no runtime mode override can leak from one test into the next.
    """
    middleware = import_plugin("middleware")
    monkeypatch.setattr(middleware, "_config_reader", lambda: {})
    monkeypatch.setattr(middleware, "_settings_provider", None)
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    middleware.reset_state()
    yield
    middleware.reset_state()
