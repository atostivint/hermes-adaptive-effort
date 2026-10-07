"""Dashboard/desktop backend: status, mode switch, probe — no prompt leaks, no network."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys

import pytest

from conftest import PLUGIN_DIR, import_plugin

API_PATH = PLUGIN_DIR / "dashboard" / "plugin_api.py"
MANIFEST_PATH = PLUGIN_DIR / "dashboard" / "manifest.json"
DESKTOP_JS = PLUGIN_DIR / "desktop" / "plugin.js"


def _load_api():
    name = "hermes_dashboard_plugin_hermes_adaptive_effort_under_test"
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
    assert payload["name"] == "hermes-adaptive-effort"
    assert payload["api"] == "plugin_api.py"
    assert API_PATH.exists()


def test_status_payload_uses_stable_schema_and_allowlist():
    payload = api.get_status_payload()
    assert payload["schema"] == "hermes-adaptive-effort.status.v2"
    assert payload["plugin"] == "hermes-adaptive-effort"
    assert payload["mode"] == "off"  # hermetic defaults; live profile must not leak in
    assert isinstance(payload["counts"], dict)
    assert isinstance(payload["sessions"], list)
    for entry in payload["sessions"]:
        assert "session_id" in entry
        assert "conversation_id" in entry


def test_status_payload_reports_the_active_route_while_mode_is_off(monkeypatch):
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: "off" if key == "mode" else default,
    )
    result = middleware.on_llm_request(
        request={"model": "local-model"}, session_id="conversation-route",
        turn_id="turn-1", provider="local-provider", model="local-model",
        api_mode="chat_completions",
    )
    assert result is None

    payload = api.get_status_payload()
    entry = next(e for e in payload["sessions"] if e["conversation_id"] == "conversation-route")
    assert (entry["state"], entry["provider"], entry["model"], entry["api_mode"]) == (
        "off", "local-provider", "local-model", "chat_completions")
    assert entry["probes"] == 0


def test_set_mode_rejects_unknown_and_changes_nothing():
    before = middleware.mode_override()
    result = api.set_mode("turbo")
    assert result["ok"] is False
    assert "unknown mode" in str(result["error"])
    assert middleware.mode_override() == before


@pytest.mark.parametrize("legacy", ["recommend", "cache_safe", "cache-safe", "inject"])
def test_dashboard_api_rejects_legacy_mode_writes(legacy):
    before = middleware.mode_override()
    result = api.set_mode(legacy)
    assert result["ok"] is False
    assert "unknown mode" in result["error"]
    assert middleware.mode_override() == before


def test_dashboard_and_desktop_expose_only_the_four_canonical_modes():
    assert api.VALID_MODES == ("auto", "once", "always", "off")
    source = DESKTOP_JS.read_text(encoding="utf-8")
    assert "const MODES = ['auto', 'once', 'always', 'off']" in source
    for legacy in ("recommend", "cache_safe", "cache-safe", "inject"):
        assert legacy not in source


@pytest.mark.parametrize("mode", ["auto", "once", "always", "off"])
def test_dashboard_mode_api_accepts_each_canonical_mode(mode):
    try:
        result = api.set_mode(mode)
        assert result["ok"] is True
        assert result["mode"] == mode
    finally:
        middleware.clear_mode_override()


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


def test_persist_failure_never_exposes_exception_text(monkeypatch):
    from hermes_cli import plugins_state

    canary = "private-operator-prompt-and-config-path"

    def fail_write(*args, **kwargs):
        raise PermissionError(canary)

    monkeypatch.setattr(plugins_state, "save_plugin_setting", fail_write)
    result = api.set_mode("auto", persist=True)
    assert result["persisted"] is False
    assert result["persist_error"] == "persist_failed"
    assert result["mode"] == "auto"  # runtime change still applies
    assert middleware.mode_override() == "auto"
    assert canary not in json.dumps(result)
    assert "PermissionError" not in json.dumps(result)


def test_probe_scores_nothing_and_stores_nothing():
    result = api.run_probe("   ")
    assert result["schema"] == "hermes-adaptive-effort.probe.v1"
    assert result["failure"] == "invalid_prompt"
    assert result["score"] is None
    assert middleware.session_state() == {}


def test_probe_never_echoes_prompt_text(monkeypatch):
    canary = "pinecone-xyzzy canary"
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: default)
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
    assert empty["schema"] == "hermes-adaptive-effort.changes.v1"
    middleware._record_effort_change("s1/turn", "medium", "high")
    feed = api.get_changes_payload()
    assert feed["stream_id"] == empty["stream_id"]  # same process, same stream
    assert (feed["latest"]["from"], feed["latest"]["to"]) == ("medium", "high")


def test_changes_feed_degrades_without_the_agent_half(monkeypatch):
    monkeypatch.setattr(api, "_agent_modules", lambda: (None, None))
    payload = api.get_changes_payload()
    assert payload["error"] == "agent_plugin_not_loaded"
    assert payload["schema"] == "hermes-adaptive-effort.changes.v1"


def test_changes_feed_degrades_when_the_feed_raises(monkeypatch):
    def boom():
        raise RuntimeError("state gone")

    monkeypatch.setattr(middleware, "effort_change_state", boom)
    payload = api.get_changes_payload()
    assert payload["error"] == "changes_failed"


def test_history_api_returns_only_compact_events_for_the_requested_conversation(
        tmp_path, monkeypatch):
    history_store = command._history_store
    monkeypatch.setattr(history_store, "_PATH_PROVIDER",
                        lambda: tmp_path / "effort-history.sqlite3")
    history_store.record_change(
        "focused", ("focused",), "turn-focused", "low", "high", "openai-codex",
        "gpt-6.1-sol", "codex_responses", "compatible",
        details={"prompt": "PRIVATE_PROMPT", "score": 1.8}, at=2.0,
    )
    history_store.record_change(
        "other", ("other",), "turn-other", "medium", "high", "remote",
        "other-model", "chat_completions", "sensitive", at=3.0,
    )

    payload = api.get_history_payload("focused")
    assert payload["schema"] == "hermes-adaptive-effort.history.v1"
    assert payload["available"] is True
    assert payload["conversation_id"] == "focused"
    assert payload["events"] == [{
        "id": payload["events"][0]["id"], "at": 2.0, "model": "gpt-6.1-sol",
        "from": "low", "to": "high", "cache_verdict": "compatible",
    }]
    assert "details" not in json.dumps(payload)
    assert "other-model" not in json.dumps(payload)
    assert "PRIVATE_PROMPT" not in json.dumps(payload)


def test_desktop_focus_helpers_select_only_the_focused_conversation():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")

    script = r"""
const fs = require('node:fs')
const assert = require('node:assert/strict')
const source = fs.readFileSync(process.argv[1], 'utf8')
const start = source.indexOf('function entryForConversation')
const end = source.indexOf('function ChangeNotifications', start)
assert(start >= 0 && end > start, 'Desktop focus helper block was not found')
const helpers = new Function(`${source.slice(start, end)}\nreturn {
  entryForConversation, effortForConversation, routeForConversation, ownerMatchesActiveBackend
}`)()

const status = { sessions: [
  { conversation_id: 'focused', state: 'decided', target: 'high', provider: 'local',
    model: 'focused-model', updated_at: 10 },
  { conversation_id: 'other', state: 'decided', target: 'low', provider: 'remote',
    model: 'newer-model', updated_at: 99 },
  { conversation_id: 'unsupported', state: 'unsupported', target: null, updated_at: 100 }
] }
assert.equal(helpers.entryForConversation(status, 'focused').model, 'focused-model')
assert.equal(helpers.effortForConversation(status, 'focused'), 'high')
assert.equal(helpers.routeForConversation(status, 'focused'), 'local · focused-model')
assert.equal(helpers.effortForConversation(status, 'unsupported'), 'N/A')
assert.equal(helpers.effortForConversation(status, 'missing'), 'N/A')

const localDefaultOwner = { connectionId: 'local', profile: '' }
assert.equal(helpers.ownerMatchesActiveBackend(localDefaultOwner, 'local', ''), true)
assert.equal(helpers.ownerMatchesActiveBackend(localDefaultOwner, null, ''), false)
assert.equal(helpers.ownerMatchesActiveBackend(localDefaultOwner, null, 'work'), false)
assert.equal(helpers.ownerMatchesActiveBackend(
  { connectionId: 'local', profile: 'work' }, 'local', 'default'), false)
"""
    result = subprocess.run(
        [node, "-e", script, str(DESKTOP_JS)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_desktop_plugin_static_contract():
    text = DESKTOP_JS.read_text(encoding="utf-8")
    chip = text.split("function AdaptiveEffortChip", 1)[1].split("export default", 1)[0]
    details = text.split("function AdaptiveEffortDetails", 1)[1].split(
        "function AdaptiveEffortChip", 1)[0]
    conversation_effort = text.split("function effortForConversation", 1)[1].split(
        "function routeForConversation", 1)[0]
    assert "const ID = 'hermes-adaptive-effort'" in text  # folder name must equal plugin id
    assert "id: ID" in text
    assert "defaultEnabled: false" in text  # opt-in, mirrors plugins.enabled gate
    assert "rest = ctx.rest" in text
    assert "rest('/status'" in text
    assert "rest('/mode'" in text
    assert "rest('/changes'" in text  # the chip's effort-change feed is a real backend route
    assert "rest(`/history?conversation_id=${encodeURIComponent(conversationId)}&limit=10`" in text
    assert "Recent applied changes" in text
    assert "Global effort activity: ${event.from} → ${event.to}" not in text
    assert "title: 'Effort changed'" in text
    change_notifications = text.split("function ChangeNotifications", 1)[1].split(
        "function DecisionNotifications", 1)[0]
    assert "function ChangeNotifications({ query, statusQuery })" in text
    assert "if (fresh.length > 0 && typeof refetchStatus === 'function')" in change_notifications
    assert "refetchStatus()" in change_notifications  # feed invalidates status; it never sets chip effort
    assert "effortNotice" not in change_notifications  # anonymous feed cannot toast for a specific chat
    assert "useValue(host.state.focusedSessionId)" in text
    assert "useValue(host.state.focusedStoredSessionId)" in text
    assert "queryKey: [ID, 'status', focusedSessionId" in chip
    assert "refetchOnMount: 'always'" in chip
    assert "refetchOnWindowFocus: true" in chip
    assert "useValue(host.state.focusedSessionOwner)" in text
    assert "effortForConversation(data, focusedConversationId)" in text
    assert "routeForConversation(data, focusedConversationId)" in text
    assert "useValue(host.state.gateway)" in chip
    assert "Route: ${route}" in details
    assert "route: ${route}" in chip
    assert "show_desktop_popup === false" in chip
    assert "Routing mode" in chip
    assert "Show details" in chip and "Hide details" in chip
    assert "onOpenChange: nextOpen =>" in chip
    assert "if (!nextOpen) setShowDetails(false)" in chip
    assert "function DecisionNotifications" in text
    assert "Effort not applied" in text
    assert "kind: 'warning'" in text
    assert "ownerMatchesBackend" in text
    assert "effort_control_unsupported" in text
    assert "reasoning_disabled" in text
    assert "No request status is available for this chat yet." in text
    assert "Why: ${reason}" in text
    assert "Request sent unchanged." in text
    assert "showActivity" in details
    assert "Latest applied effort:" not in details
    assert "Tip" in text
    for help_text in (
        "Recheck each turn when supported; otherwise reuse per route.",
        "Keep one decision per route in this conversation.",
        "Score every new user message.",
        "No scoring or request changes.",
    ):
        assert help_text in text
    assert "area: 'panes'" not in text
    assert "AdaptiveEffortPane" not in text
    assert "status-pane" not in text
    assert "Adaptive Effort: Status" not in text
    assert "${ID}.status" not in text
    for detail in (
        "This chat",
        "Effort: ${effort}",
        "Route: ${route}",
        "Scorer",
        "${credential} key",
        "Gateway ${gateway}",
        "Activity ▸",
        "Activity ▾",
        "Counts and latest result across all conversations.",
        "Sessions ${data?.counts?.sessions",
        "Last: ${last.state} · effort",
        "No recent status.",
    ):
        assert detail in details
    assert "const [showActivity, setShowActivity] = useState(false)" in details
    assert "Latest applied effort:" not in details
    assert "changesQuery.data" not in conversation_effort
    assert "changesQuery.data" not in chip
    assert "entry?.conversation_id === conversationId" in text
    assert "focusedOwner?.connectionId, focusedOwner?.profile" in text
    owner_match = text.split("function ownerMatchesActiveBackend", 1)[1].split(
        "function ChangeNotifications", 1)[0]
    assert "function ownerMatchesActiveBackend(owner, connectionId, profile)" in text
    assert "const ownerMatchesBackend = ownerMatchesActiveBackend(" in chip
    assert "focusedOwner, activeConnectionId, activeProfile" in chip
    assert "const activeConnectionId = typeof connectionId === 'string' ? connectionId.trim() : ''" in owner_match
    assert "return ownerConnectionId === activeConnectionId &&" in owner_match
    assert "(ownerProfile || 'default') === (activeProfile || 'default')" in owner_match
    assert "latest?.state === 'decided'" in text  # unsupported / in-flight focus shows N/A
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
