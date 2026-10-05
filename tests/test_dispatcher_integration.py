"""Integration: the plugin driven by the REAL Hermes middleware dispatcher.

This is the one test that does not call our callback directly. It boots a
throwaway ``HERMES_HOME``, copies the payload into ``<home>/plugins/hermes-adaptive-effort``,
lets Hermes' own ``PluginManager`` discover and register it, then enters through
``hermes_cli.middleware.apply_llm_request_middleware`` — the exact function
``agent/turn_api_request.py`` calls before building a provider request.

Jev is a fake (no socket is ever opened: ``no_network`` proves it) and the
provider request is a fixed fixture shaped like a recorded OpenAI-compatible
turn, so the assertions are about wiring, not about a live classifier.
"""

from __future__ import annotations

import json
import os
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
    # Hermes canonicalizes HERMES_HOME to lowercase on Windows; compare path
    # spellings with the platform's case rules instead of raw string equality.
    prefix = os.path.normcase(str(home / "plugins"))
    for name, mod in list(sys.modules.items()):
        filename = getattr(mod, "__file__", "") or ""
        if os.path.normcase(filename).startswith(prefix) and name.endswith(suffix):
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
        PLUGIN_DIR, home / "plugins" / "hermes-adaptive-effort",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    bundled = home / "bundled_plugins"
    bundled.mkdir()
    (home / "config.yaml").write_text(
        json.dumps({
            "plugins": {
                "enabled": ["hermes-adaptive-effort"],
                "entries": {"hermes-adaptive-effort": {"settings": {
                    "mode": "auto", "prompt_sharing_provider": "jev"}}},
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
    assert result.trace and result.trace[0]["source"] == "hermes-adaptive-effort"

    # Registered through the real PluginContext, nothing more and nothing less.
    plugin = dispatched["manager"]._plugins["hermes-adaptive-effort"]
    assert plugin.middleware_registered == ["llm_request"]
    assert "hermes-adaptive-effort" in plugin.commands_registered
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
    assert calls["n"] == 0          # off means *no* scorer call, not a silent one
    assert middleware.session_state() == {}


# ── criterion 3: one decision per TURN, re-clamped when the route changes ───
#
# These three go through the same real dispatcher: the point is that the reuse rule
# survives the host's own plumbing (context keys, deep copies, the trace it builds).

def _recorded():
    """A fresh deep copy of the recorded turn, so a rewrite cannot leak sideways."""
    return json.loads(json.dumps(RECORDED_REQUEST))


def _counting_factory(state, score=1.9, failure=None):
    def factory(**kwargs):
        state["n"] += 1
        return _FakeJev(score, failure)
    return factory


def test_real_dispatcher_reuses_one_decision_across_a_tool_loop(dispatched, no_network):
    """A tool loop is several requests of ONE turn: one probe, one decision."""
    from hermes_cli.middleware import apply_llm_request_middleware

    middleware = dispatched["middleware"]
    middleware.reset_state()
    probes = {"n": 0}
    middleware._classifier_factory = _counting_factory(probes, 1.9)

    for call_count in (1, 2, 3):
        result = apply_llm_request_middleware(
            _recorded(),
            session_id="sess-int-loop",
            provider="openrouter",
            model="openrouter/meta/llama-3.3-70b-instruct",
            api_mode="chat",
            turn_id="turn-1",
            api_call_count=call_count,
        )
        assert result.changed is True
        assert result.payload["extra_body"]["reasoning"]["effort"] == "high"

    entry = middleware.session_state()["sess-int-loop/turn-1"]
    assert entry["requests"] == 3
    assert entry["probes"] == 1
    assert probes["n"] == 1


def test_real_dispatcher_reclamps_a_stale_target_when_the_route_changes(
        dispatched, no_network):
    """A provider fallback lands on Moonshot K3 in the SAME turn.

    K3 declares exactly low/high/max, so replaying the recorded ``medium`` would be a
    vendor-side 400. The recorded label is re-clamped instead, without a second probe.
    """
    from hermes_cli.middleware import apply_llm_request_middleware

    middleware = dispatched["middleware"]
    middleware.reset_state()
    probes = {"n": 0}
    middleware._classifier_factory = _counting_factory(probes, 1.0)  # -> medium

    first = apply_llm_request_middleware(
        _recorded(), session_id="sess-int-fallback", provider="openrouter",
        model="openrouter/meta/llama-3.3-70b-instruct", api_mode="chat",
        turn_id="turn-fb", api_call_count=1,
    )
    # The wide route already sits at `medium`: no change reported, but recorded.
    assert first.changed is False
    entry = middleware.session_state()["sess-int-fallback/turn-fb"]
    assert entry["state"] == "decided"
    assert entry["target"] == "medium"

    second = apply_llm_request_middleware(
        _recorded(), session_id="sess-int-fallback", provider="moonshot",
        model="moonshot/kimi-k3", api_mode="chat",
        turn_id="turn-fb", api_call_count=2,
    )

    assert second.changed is True
    assert second.payload["extra_body"]["reasoning"]["effort"] == "high"
    # Re-read: session_state() hands out snapshots, so the entry must be fetched again.
    entry = middleware.session_state()["sess-int-fallback/turn-fb"]
    assert entry["target"] == "high"
    assert entry["provider"] == "moonshot"
    assert entry["model"] == "moonshot/kimi-k3"
    assert entry["probes"] == 1
    assert probes["n"] == 1


def test_real_dispatcher_fails_open_and_does_not_retry_a_failed_turn(
        dispatched, no_network):
    """A classifier failure must leave the request alone, and cost one probe only."""
    from hermes_cli.middleware import apply_llm_request_middleware

    middleware = dispatched["middleware"]
    middleware.reset_state()
    probes = {"n": 0}
    middleware._classifier_factory = _counting_factory(
        probes, score=None, failure="classifier_timeout")

    result = apply_llm_request_middleware(
        _recorded(), session_id="sess-int-fail", provider="openrouter",
        model="openrouter/meta/llama-3.3-70b-instruct", api_mode="chat",
        turn_id="turn-fail", api_call_count=1,
    )

    assert result.changed is False
    assert result.payload["extra_body"]["reasoning"]["effort"] == "medium"
    entry = middleware.session_state()["sess-int-fail/turn-fail"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "classifier_timeout"
    assert entry["probes"] == 1

    again = apply_llm_request_middleware(
        _recorded(), session_id="sess-int-fail", provider="openrouter",
        model="openrouter/meta/llama-3.3-70b-instruct", api_mode="chat",
        turn_id="turn-fail", api_call_count=2,
    )

    assert again.changed is False
    assert entry["probes"] == 1          # the failure is not retried inside the turn
    assert probes["n"] == 1
    assert dispatched["command"].handle("status json").count("sess-int-fail") >= 1


def test_real_dispatcher_marks_an_unwritable_request_unsupported(dispatched, no_network):
    """Reasoning disabled: there is nothing to rewrite, and no scorer call is spent."""
    from hermes_cli.middleware import apply_llm_request_middleware

    middleware = dispatched["middleware"]
    middleware.reset_state()
    probes = {"n": 0}
    middleware._classifier_factory = _counting_factory(probes)

    request = _recorded()
    request["extra_body"]["reasoning"]["enabled"] = False

    result = apply_llm_request_middleware(
        request, session_id="sess-int-none", provider="openrouter",
        model="openrouter/meta/llama-3.3-70b-instruct", api_mode="chat",
        turn_id="turn-none", api_call_count=1,
    )

    assert result.changed is False
    assert result.payload["extra_body"]["reasoning"]["effort"] == "medium"
    entry = middleware.session_state()["sess-int-none/turn-none"]
    assert entry["state"] == "unsupported"
    assert entry["probes"] == 0
    assert probes["n"] == 0
