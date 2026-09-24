"""Shared fixtures for Jev-Auto plugin tests.

Loads the plugin payload (``jev-auto/``) as a real package (the same sibling-module
layout Hermes' plugin loader builds) and guarantees unit tests never touch the network.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "jev-auto"
PLUGIN_PACKAGE = "hermes_plugin_jev_auto"


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
