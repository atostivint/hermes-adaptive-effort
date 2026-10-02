"""Dashboard/desktop backend: status, mode switch, probe — no prompt leaks, no network."""

from __future__ import annotations

import importlib.util
import json
import re
import sys

import pytest

from conftest import PLUGIN_DIR, import_plugin

API_PATH = PLUGIN_DIR / "dashboard" / "plugin_api.py"
MANIFEST_PATH = PLUGIN_DIR / "dashboard" / "manifest.json"
DESKTOP_JS = PLUGIN_DIR / "desktop" / "plugin.js"


def _load_api():
    name = "hermes_dashboard_plugin_jev_auto_effort_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(API_PATH))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = _load_api()
middleware = import_plugin("middleware")
command = import_plugin("command")  # both halves: mirrors production, where __init__
# imports command+middleware together, so _agent_modules() finds the shared live
# pair instead of falling back to a duplicate from-disk load with separate state


@pytest.fixture(autouse=True)
def _pin_hermetic_modules():
    """Pin the hermetic copies for every test in this module.

    ``test_dispatcher_integration`` boots a real PluginManager whose payload modules
    stay in ``sys.modules`` by design; without pinning, ``_agent_modules()`` would
    (correctly, production behaviour) prefer that live pair — with its ``auto`` mode
    and leftover classifier — over these hermetic copies.
    """
    api._PINNED = (middleware, command)
    yield
    api._PINNED = None


def test_manifest_declares_backend_api():
    assert MANIFEST_PATH.exists()
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert payload["name"] == "jev-auto-effort"
    assert payload["api"] == "plugin_api.py"
    assert API_PATH.exists()


def test_status_payload_uses_stable_schema_and_allowlist():
    payload = api.get_status_payload()
    assert payload["schema"] == "jev-auto-effort.status.v1"
    assert payload["plugin"] == "jev-auto-effort"
    assert payload["mode"] == "off"  # hermetic defaults; live profile must not leak in
    assert isinstance(payload["counts"], dict)
    assert isinstance(payload["sessions"], list)
    for entry in payload["sessions"]:
        assert "session_id" in entry
        assert "conversation_id" in entry


def test_set_mode_rejects_unknown_and_changes_nothing():
    before = middleware.mode_override()
    result = api.set_mode("turbo")
    assert result["ok"] is False
    assert "unknown mode" in str(result["error"])
    assert middleware.mode_override() == before


def test_set_mode_runtime_only_by_default():
    result = api.set_mode("auto")
    try:
        assert result["ok"] is True
        assert result["mode"] == "auto"
        assert result["before"] == "off"
        assert "persisted" not in result
        assert middleware.mode_override() == "auto"
    finally:
        middleware.clear_mode_override()
    assert middleware.mode_override() is None


def test_probe_scores_nothing_and_stores_nothing():
    result = api.run_probe("   ")
    assert result["schema"] == "jev-auto-effort.probe.v1"
    assert result["failure"] == "invalid_prompt"
    assert result["score"] is None
    assert middleware.session_state() == {}


def test_probe_never_echoes_prompt_text(monkeypatch):
    canary = "pinecone-xyzzy canary"
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **kw: type("C", (), {"classify": lambda self, p: 1.0})())
    result = api.run_probe(f"how do I rotate a token {canary}")
    assert result["label"] == "medium"
    assert middleware.session_state() == {}
    assert canary not in json.dumps([result, middleware.session_state()])


def test_probe_clips_long_text():
    result = api.run_probe("x" * (api.MAX_PROBE_CHARS + 500))
    assert result["text_chars"] == api.MAX_PROBE_CHARS



def test_changes_feed_mirrors_the_live_agent_half():
    empty = api.get_changes_payload()
    assert empty["events"] == [] and empty["latest"] is None
    assert empty["schema"] == "jev-auto-effort.changes.v1"
    middleware._record_effort_change("s1/turn", "medium", "high")
    feed = api.get_changes_payload()
    assert feed["stream_id"] == empty["stream_id"]  # same process, same stream
    assert (feed["latest"]["from"], feed["latest"]["to"]) == ("medium", "high")


def test_changes_feed_degrades_without_the_agent_half(monkeypatch):
    monkeypatch.setattr(api, "_agent_modules", lambda: (None, None))
    payload = api.get_changes_payload()
    assert payload["error"] == "agent_plugin_not_loaded"
    assert payload["schema"] == "jev-auto-effort.changes.v1"


def test_changes_feed_degrades_when_the_feed_raises(monkeypatch):
    def boom():
        raise RuntimeError("state gone")

    monkeypatch.setattr(middleware, "effort_change_state", boom)
    payload = api.get_changes_payload()
    assert payload["error"] == "changes_failed"


def test_desktop_plugin_static_contract():
    text = DESKTOP_JS.read_text(encoding="utf-8")
    chip = text.split("function JevChip", 1)[1].split("function JevPane", 1)[0]
    assert "const ID = 'jev-auto-effort'" in text  # folder name must equal plugin id
    assert "id: ID" in text
    assert "defaultEnabled: false" in text  # opt-in, mirrors plugins.enabled gate
    assert "rest = ctx.rest" in text
    assert "rest('/status'" in text
    assert "rest('/mode'" in text
    assert "rest('/changes'" in text  # the chip's effort-change feed is a real backend route
    assert "useValue(host.state.focusedSessionId)" in text
    assert "useValue(host.state.focusedSessionOwner)" in text
    assert "effortForConversation(data, focusedSessionId)" in text
    assert "entry?.conversation_id === conversationId" in text
    assert "focusedOwner?.connectionId === activeConnectionId" in text
    assert "focusedOwner?.profile === activeProfile" in text
    assert "focusedOwner?.connectionId, focusedOwner?.profile" in text
    assert "changesQuery.data?.latest" not in chip  # a global feed cannot pick the chip effort
    assert "latest?.state === 'decided'" in text  # unsupported / in-flight focus shows N/A
    assert "latest applied effort (all conversations)" in text
    for allowed in ("@hermes/plugin-sdk", "react", "react/jsx-runtime"):
        assert allowed in text
    assert "localStorage" not in text  # UI prefs belong to ctx.storage, decisions to backend
    assert "XMLHttpRequest" not in text  # ctx.rest is the door, never raw transport
    assert not re.search(r"(?<![\w.])fetch\s*\(", text), "ctx.rest is the door, never fetch"
    imports = set(re.findall(r"from\s*['\"]([^'\"]+)['\"]", text))
    assert imports <= {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}, (
        f"disk plugins resolve only the SDK + react: {sorted(imports)}")
    assert not re.search(r"<[A-Za-z][A-Za-z0-9]*(\s+[^<>]*?)?/?>", text.replace("=>", "")), (
        "disk file is uncompiled: jsx()/jsxs() calls only, no JSX syntax")
