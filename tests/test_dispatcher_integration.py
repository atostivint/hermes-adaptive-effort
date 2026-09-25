"""Integration: the plugin driven by the REAL Hermes middleware dispatcher.

This is the one test that does not call our callback directly. It boots a
throwaway ``HERMES_HOME``, copies the payload into ``<home>/plugins/jev-auto``,
lets Hermes' own ``PluginManager`` discover and register it, then enters through
``hermes_cli.middleware.apply_llm_request_middleware`` — the exact function
``agent/turn_api_request.py`` calls before building a provider request.

Jev is a fake (no socket is ever opened: ``no_network`` proves it) and the
provider request is a fixed fixture shaped like a recorded OpenAI-compatible
turn, so the assertions are about wiring, not about a live classifier.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from conftest import PLUGIN_DIR

pytest.importorskip(
    "hermes_cli.plugins",
    reason="Hermes source tree not importable — see tests/conftest.py",
)


class _FakeJev:
    """Deterministic Jev transport: score 1.9 -> 'high', no I/O."""

    def __init__(self, score=1.9, failure=None):
        self.score = score
        self.failure = failure

    def classify(self, prompt):
        return self.score if self.failure is None else None

    def classify_detail(self, prompt):
        return self.score, self.failure


# Recorded-shape OpenAI-compatible request: reasoning already on, effort pinned.
RECORDED_REQUEST = {
    "model": "openrouter/meta/llama-3.3-70b-instruct",
    "messages": [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "Design a multi-region failover plan."},
    ],
    "temperature": 0.2,
    "extra_body": {"reasoning": {"enabled": True, "effort": "medium"}},
}


def _payload_module(home: Path, suffix: str):
    """The module Hermes loaded from *this* home's payload (not our test copy)."""
    prefix = str(home / "plugins")
    for name, mod in list(sys.modules.items()):
        filename = getattr(mod, "__file__", "") or ""
        if filename.startswith(prefix) and name.endswith(suffix):
            return mod
    raise AssertionError(f"payload module {suffix!r} was not loaded from {home}")


@pytest.fixture(scope="module")
def dispatched(tmp_path_factory):
    """Discover the plugin under a real Hermes home and hand back its parts."""
    import os

    from hermes_cli.plugins import get_plugin_manager

    home = tmp_path_factory.mktemp("jev_home")
    (home / "plugins").mkdir()
    shutil.copytree(
        PLUGIN_DIR, home / "plugins" / "jev-auto",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    bundled = home / "bundled_plugins"
    bundled.mkdir()
    (home / "config.yaml").write_text(
        json.dumps({
            "plugins": {
                "enabled": ["jev-auto"],
                "entries": {"jev-auto": {"settings": {"mode": "auto"}}},
            },
        }),
        encoding="utf-8",
    )

    saved = {key: os.environ.get(key)
             for key in ("HERMES_HOME", "HERMES_BUNDLED_PLUGINS")}
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(bundled)
    try:
        manager = get_plugin_manager()
        manager.discover_and_load()
        middleware = _payload_module(home, ".middleware")
        command = _payload_module(home, ".command")
        yield {
            "home": home,
            "manager": manager,
            "middleware": middleware,
            "command": command,
        }
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_real_dispatcher_discovers_registers_and_rewrites(dispatched, no_network):
    from hermes_cli.middleware import apply_llm_request_middleware

    middleware = dispatched["middleware"]
    middleware.reset_state()
    middleware._classifier_factory = lambda **kwargs: _FakeJev(1.9)

    request = json.loads(json.dumps(RECORDED_REQUEST))  # deep copy of the fixture
    result = apply_llm_request_middleware(
        request,
        session_id="sess-int-1",
        provider="openrouter",
        model="openrouter/meta/llama-3.3-70b-instruct",
        api_mode="chat",
    )

    # The rewrite, on a copy: the caller's own payload is never mutated.
    assert result.changed is True
    assert result.payload["extra_body"]["reasoning"]["effort"] == "high"
    assert request["extra_body"]["reasoning"]["effort"] == "medium"
    assert result.original_payload["extra_body"]["reasoning"]["effort"] == "medium"
    assert result.trace and result.trace[0]["source"] == "jev-auto"

    # Registered through the real PluginContext, nothing more and nothing less.
    plugin = dispatched["manager"]._plugins["jev-auto"]
    assert plugin.middleware_registered == ["llm_request"]
    assert "jev-auto" in plugin.commands_registered
    assert "on_session_end" in plugin.hooks_registered
    assert not getattr(plugin, "tools_registered", None)

    # The session state the status command reports, straight from that run.
    entry = middleware.session_state()["sess-int-1"]
    assert entry["state"] == "decided"
    assert entry["label"] == "high"
    assert entry["target"] == "high"
    assert entry["score"] == pytest.approx(1.9)
    assert entry["requests"] == 1
    assert entry["probes"] == 1
    assert entry["elapsed_ms"] >= 0

    # ... and the documented status payload describes the very same decision.
    payload = json.loads(dispatched["command"].handle("status json"))
    assert payload["mode"] == "auto"
    assert payload["counts"]["sessions"] == 1
    assert payload["counts"]["in_flight"] == 0
    assert payload["last"]["session_id"] == "sess-int-1"
    assert payload["last"]["target"] == "high"
    assert payload["last"]["score"] == pytest.approx(1.9)


def test_real_dispatcher_leaves_the_request_untouched_when_mode_is_off(dispatched, no_network):
    from hermes_cli.middleware import apply_llm_request_middleware

    middleware = dispatched["middleware"]
    middleware.reset_state()
    calls = {"n": 0}

    def _counting_factory(**kwargs):
        calls["n"] += 1
        return _FakeJev(0.2)

    middleware._classifier_factory = _counting_factory
    # Force the documented default without touching the shared config file.
    original_provider = middleware._settings_provider
    middleware._settings_provider = lambda key, default=None: (
        "off" if key == "mode" else default)
    try:
        request = json.loads(json.dumps(RECORDED_REQUEST))
        result = apply_llm_request_middleware(
            request,
            session_id="sess-int-off",
            provider="openrouter",
            model="openrouter/meta/llama-3.3-70b-instruct",
            api_mode="chat",
        )
    finally:
        middleware._settings_provider = original_provider

    assert result.changed is False
    assert result.payload["extra_body"]["reasoning"]["effort"] == "medium"
    assert result.trace == []
    assert calls["n"] == 0          # off means *no* Jev call, not a silent one
    assert middleware.session_state() == {}
