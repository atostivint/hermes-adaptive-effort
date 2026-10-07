"""Public Desktop decision events stay prompt-free and fail-open."""

from __future__ import annotations

import copy
import sys
import types

from conftest import import_plugin

middleware = import_plugin("middleware")
command = import_plugin("command")

SESSION = "DESKTOP-EVENT-SESSION"
ROUTE = {
    "provider": "openrouter",
    "model": "openrouter/x/y",
    "api_mode": "chat_completions",
}


class FakeJev:
    def __init__(self, score=1.9, failure=None):
        self.score = score
        self.failure = failure

    def classify_detail(self, prompt):
        return self.score, self.failure


def capture_events(monkeypatch):
    events = []
    module = types.ModuleType("hermes_cli.plugin_events")
    module.broadcast_plugin_event = lambda plugin, name, payload: events.append(
        (plugin, name, copy.deepcopy(payload)))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events", module)
    return events


def configure(monkeypatch, mode="auto", classifier=None):
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: mode if key == "mode" else default)
    if classifier is not None:
        monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: classifier)


def invoke(**kwargs):
    request = kwargs.pop("request", {
        "model": ROUTE["model"],
        "messages": [{"role": "user", "content": "PRIVATE TASK TEXT"}],
        "extra_body": {"reasoning": {"enabled": True, "effort": "medium"}},
    })
    return middleware.on_llm_request(
        session_id=SESSION, turn_id="turn-1", provider=ROUTE["provider"],
        model=ROUTE["model"], api_mode=ROUTE["api_mode"],
        request=request,
        **kwargs,
    )


def payloads(events):
    assert all((plugin, name) == (middleware.PLUGIN_ID, "decision.updated")
               for plugin, name, _ in events)
    return [payload for _, _, payload in events]


def test_applied_rewrite_emits_allowlisted_route_and_stable_marker(monkeypatch):
    events = capture_events(monkeypatch)
    configure(monkeypatch, classifier=FakeJev())

    first = invoke()
    second = invoke()

    assert first["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert second["request"]["extra_body"]["reasoning"]["effort"] == "high"
    emitted = payloads(events)
    applied = [event["applied"] for event in emitted if event["applied"]]
    assert applied[0] == applied[-1]
    assert applied[0]["from"] == "medium"
    assert applied[0]["to"] == "high"
    assert emitted[-1]["route"] == ROUTE
    assert emitted[-1]["runtime_session_id"] == SESSION
    assert emitted[-1]["schema"] == "hermes-adaptive-effort.desktop-status.v1"
    assert emitted[-1]["status"]["state"] == "decided"
    assert set(emitted[-1]["status"]) == set(command._ENTRY_FIELDS)
    assert "PRIVATE TASK TEXT" not in repr(emitted)


def test_off_status_and_compute_child_sync_capability(monkeypatch):
    events = capture_events(monkeypatch)
    configure(monkeypatch, mode="off")
    monkeypatch.setenv("HERMES_COMPUTE_HOST_CHILD", "1")

    assert invoke() is None

    event = payloads(events)[-1]
    assert event["status"]["state"] == "off"
    assert event["status"]["target"] is None
    assert event["selector_sync_supported"] is False
    assert event["applied"] is None


def test_unsupported_and_failed_decisions_are_published(monkeypatch):
    events = capture_events(monkeypatch)
    configure(monkeypatch, classifier=FakeJev(score=None, failure="timeout"))

    invoke(request={
        "model": ROUTE["model"],
        "messages": [{"role": "user", "content": "PRIVATE TASK TEXT"}],
        "extra_body": {"reasoning": {"enabled": False, "effort": "medium"}},
    })
    unsupported = payloads(events)[-1]
    assert unsupported["status"]["state"] == "unsupported"
    assert unsupported["status"]["failure"] == "reasoning_disabled"

    middleware.reset_state()
    events.clear()
    invoke()
    failed = payloads(events)[-1]
    assert failed["status"]["state"] == "failed"
    assert failed["status"]["failure"] == "timeout"
    assert failed["applied"] is None


def test_session_boundary_emits_clear_with_new_revision(monkeypatch):
    events = capture_events(monkeypatch)
    configure(monkeypatch, mode="off")
    invoke()
    middleware.on_session_finalize(session_id=SESSION)

    emitted = payloads(events)
    clear = emitted[-1]
    assert clear["clear"] is True
    assert clear["status"] is None
    assert clear["runtime_session_id"] == SESSION
    assert clear["revision"] > emitted[-2]["revision"]


def test_event_bridge_failure_does_not_change_rewrite(monkeypatch):
    module = types.ModuleType("hermes_cli.plugin_events")
    module.broadcast_plugin_event = lambda *args: (_ for _ in ()).throw(RuntimeError("offline"))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugin_events", module)
    configure(monkeypatch, classifier=FakeJev())

    result = invoke()

    assert result["request"]["extra_body"]["reasoning"]["effort"] == "high"
