"""Per-request cost: one config read, memoised registry lookups, backwards tool-result scan."""

from __future__ import annotations

import hashlib
import json
import random
import sys
import types

from conftest import import_plugin

middleware = import_plugin("middleware")
effort = import_plugin("effort")
tool_errors = import_plugin("tool_errors")

PID = middleware.PLUGIN_ID


# ── 1. a single config read per _settings() call ─────────────────────────────

def test_settings_reads_the_config_once_per_call(monkeypatch):
    reads = []

    def reader():
        reads.append(1)
        return {"plugins": {"entries": {PID: {"settings": {"mode": "always", "max_turns": 7}}}}}

    monkeypatch.setattr(middleware, "_config_reader", reader)
    first = middleware._settings()
    assert len(reads) == 1 and first["mode"] == "always" and first["max_turns"] == 7
    middleware._settings()
    assert len(reads) == 2  # no TTL: every call re-reads


def test_config_edit_is_picked_up_on_the_next_call(monkeypatch):
    config = {"plugins": {"entries": {PID: {"settings": {"mode": "auto"}}}}}
    monkeypatch.setattr(middleware, "_config_reader", lambda: config)
    assert middleware._settings()["mode"] == "auto"
    config["plugins"]["entries"][PID]["settings"]["mode"] = "once"
    assert middleware._settings()["mode"] == "once"


def test_ctx_provider_is_read_in_one_pass_with_legacy_fallback(monkeypatch):
    loads = []
    config = {"plugins": {"entries": {PID: {
        "settings": {"mode": "always", "timeout_s": ""}, "config": {"max_turns": 5}}}}}

    def load():
        loads.append(1)
        return config

    def get_config(key, default=None):  # what ctx.get_config would do, one load per key
        load()
        entry = config["plugins"]["entries"][PID]
        return entry["settings"].get(key, entry["config"].get(key, default))

    monkeypatch.setattr(middleware, "_config_reader", load)
    monkeypatch.setattr(middleware, "_settings_provider", get_config)
    monkeypatch.setattr(middleware, "_settings_ctx_reader", get_config)
    settings = middleware._settings()
    assert len(loads) == 1
    assert settings["mode"] == "always" and settings["max_turns"] == 5
    assert settings["timeout_s_configured"] == 0.0  # empty string -> default


def test_foreign_provider_keeps_its_per_key_contract(monkeypatch):
    seen = []

    def provider(key, default=None):
        seen.append(key)
        return {"mode": "once"}.get(key, default)

    monkeypatch.setattr(middleware, "_settings_provider", provider)
    monkeypatch.setattr(middleware, "_settings_ctx_reader", lambda key, default=None: None)
    assert middleware._settings()["mode"] == "once"
    assert "mode" in seen and len(seen) > 5


# ── 2. memoised registry lookups never cache a failed import ─────────────────

def test_route_choice_lookup_is_memoised():
    effort.clear_caches()
    key = ("opencode-go", "glm-5.3", "chat_completions", None)
    first = effort.route_choice_levels(*key)
    assert effort._CHOICE_CACHE and effort.route_choice_levels(*key) is first


def test_failed_core_import_is_not_cached(monkeypatch):
    effort.clear_caches()
    monkeypatch.setitem(sys.modules, "agent.reasoning_effort", None)  # import raises
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))  # no attribute either
    route = ("openai-codex", "gpt-6.1-sol", "codex_responses", None)
    assert effort.route_choice_levels(*route) == ()
    assert not effort._CHOICE_CACHE
    assert effort.wire_efforts("zai", "glm-5.3") == ()
    assert not effort._WIRE_CACHE

    fake = types.ModuleType("agent.reasoning_effort")
    fake.codex_supported_efforts = lambda model: ("low", "medium", "high")
    fake.GLM53_EFFORTS = ("low", "high")
    fake._KIMI_K3_SLUG_RE = __import__("re").compile("^$")
    monkeypatch.setitem(sys.modules, "agent.reasoning_effort", fake)
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    sys.modules["agent"].reasoning_effort = fake
    assert effort.route_choice_levels(*route) == ("low", "medium", "high")
    assert effort.wire_efforts("zai", "glm-5.3") == ("low", "high")


def test_reset_state_drops_the_memos():
    effort.route_choice_levels("opencode-go", "glm-5.3", "chat_completions")
    tool_errors._result_fingerprint(json.dumps({"error": "boom"}))
    middleware.reset_state()
    assert not effort._CHOICE_CACHE and not tool_errors._FINGERPRINT_MEMO


# ── 3. backwards scan equals the old forward scan ────────────────────────────

def _old_events_from_messages(messages):
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, dict) and tool_errors._is_turn_boundary(message):
            start = index + 1
    events = []
    for message in messages[start:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "tool":
            events.append(("result", tool_errors.result_error_text(message.get("content"))))
            continue
        if role == "assistant":
            calls = message.get("tool_calls")
            for call in calls if isinstance(calls, list) else []:
                function = call.get("function") if isinstance(call, dict) else None
                name = (function or {}).get("name") if isinstance(function, dict) else None
                if name in tool_errors.EDIT_TOOL_NAMES:
                    events.append(("edit", None))
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in tool_errors.EDIT_TOOL_NAMES:
                    events.append(("edit", None))
                elif block.get("type") == "tool_result":
                    flagged = block.get("is_error") is True
                    events.append(("result", tool_errors.result_error_text(
                        block.get("content"), flagged)))
    return events


def _old_events_from_input(items):
    start = 0
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("role") == "user":
            start = index + 1
    events = []
    for item in items[start:]:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "function_call" and item.get("name") in tool_errors.EDIT_TOOL_NAMES:
            events.append(("edit", None))
        elif kind == "function_call_output":
            events.append(("result", tool_errors.result_error_text(item.get("output"))))
    return events


def _old_repeated(request):
    messages = request.get("messages")
    if isinstance(messages, list) and messages:
        events = _old_events_from_messages(messages)
    elif isinstance(request.get("input"), list):
        events = _old_events_from_input(request["input"])
    else:
        return []
    positions = [i for i, (kind, _) in enumerate(events) if kind == "result"]
    if len(positions) > tool_errors.LOOKBACK_RESULTS:
        events = events[positions[-tool_errors.LOOKBACK_RESULTS]:]
    last_seen, last_edit, repeated = {}, -1, []
    for index, (kind, text) in enumerate(events):
        if kind == "edit":
            last_edit = index
            continue
        digest = tool_errors.fingerprint(text)
        if digest is None:
            continue
        previous = last_seen.get(digest)
        if previous is not None and last_edit > previous and digest not in repeated:
            repeated.append(digest)
        last_seen[digest] = index
    return repeated


def _random_result(rng):
    pick = rng.choice(["err_a", "err_b", "err_c", "ok", "plain", "exit", "refusal", "empty"])
    if pick.startswith("err_"):
        return json.dumps({"error": f"failure {pick} at line {rng.randint(1, 99)}"})
    if pick == "ok":
        return json.dumps({"success": True})
    if pick == "exit":
        return json.dumps({"output": "FAILED x", "exit_code": rng.choice([0, 1, 2])})
    if pick == "refusal":
        return json.dumps({"error": "no", "guardrail_refusal": True})
    return "" if pick == "empty" else "not json"


def _random_chat(rng):
    messages = []
    for _ in range(rng.randint(0, 6)):
        messages.append({"role": "user", "content": "hi"})
    for index in range(rng.randint(0, 60)):
        roll = rng.random()
        if roll < 0.04:
            messages.append({"role": "user", "content": "new turn"})
        elif roll < 0.5:
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{index}", "type": "function",
                 "function": {"name": rng.choice(["patch", "terminal", "write_file"]),
                              "arguments": "{}"}}]})
            messages.append({"role": "tool", "tool_call_id": f"c{index}",
                             "content": _random_result(rng)})
        elif roll < 0.7:
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "name": rng.choice(["patch", "bash"]), "id": "u"}]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "content": _random_result(rng),
                 "is_error": rng.random() < 0.5}]})
        elif roll < 0.75:
            messages.append("junk")
        else:
            messages.append({"role": "assistant", "content": "thinking"})
    return {"messages": messages}


def _random_input(rng):
    items = [{"role": "user", "content": "go"}]
    for index in range(rng.randint(0, 60)):
        roll = rng.random()
        if roll < 0.03:
            items.append({"role": "user", "content": "again"})
        elif roll < 0.5:
            items.append({"type": "function_call", "call_id": f"c{index}",
                          "name": rng.choice(["patch", "terminal"]), "arguments": "{}"})
            items.append({"type": "function_call_output", "call_id": f"c{index}",
                          "output": _random_result(rng)})
        elif roll < 0.55:
            items.append(None)
    return {"input": items}


def test_backwards_scan_matches_the_forward_scan():
    rng = random.Random(1234)
    nonempty = 0
    for _ in range(400):
        request = _random_chat(rng) if rng.random() < 0.6 else _random_input(rng)
        expected = _old_repeated(request)
        nonempty += bool(expected)
        assert tool_errors.repeated_error_fingerprints(request) == expected
    assert nonempty > 20  # the generator really exercises repeats


def test_scan_stops_at_the_lookback_and_the_turn_boundary():
    big = [{"role": "user", "content": "old turn"}]
    for index in range(40):
        big.append({"role": "tool", "tool_call_id": str(index),
                    "content": json.dumps({"error": f"e{index}"})})
    big.append({"role": "user", "content": "new turn"})
    assert tool_errors._events_from_messages(big) == []
    big.append({"role": "tool", "tool_call_id": "z", "content": json.dumps({"error": "x"})})
    big.append({"role": "tool", "tool_call_id": "z2", "content": json.dumps({"error": "x"})})
    assert len(tool_errors._events_from_messages(big)) == 2


def test_scan_parses_only_the_kept_results(monkeypatch):
    calls = []
    real = tool_errors.result_error_text
    monkeypatch.setattr(tool_errors, "result_error_text",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    tool_errors.clear_memo()
    messages = [{"role": "user", "content": "go"}]
    for index in range(50):
        messages.append({"role": "tool", "tool_call_id": str(index),
                         "content": json.dumps({"error": f"e{index}"})})
    tool_errors.repeated_error_fingerprints({"messages": messages})
    assert len(calls) == tool_errors.LOOKBACK_RESULTS
    tool_errors.repeated_error_fingerprints({"messages": messages})
    assert len(calls) == tool_errors.LOOKBACK_RESULTS  # second pass: all memoised


def test_memo_keeps_only_digests_never_text():
    tool_errors.clear_memo()
    secret = "SECRET-ERROR-TEXT-1234"
    tool_errors._result_fingerprint(json.dumps({"error": secret}))
    for key, value in tool_errors._FINGERPRINT_MEMO.items():
        assert secret not in key and (value is None or len(value) == 64)
    assert hashlib.sha256(json.dumps({"error": secret}).encode()).hexdigest() in {
        k.rstrip("!") for k in tool_errors._FINGERPRINT_MEMO}


# ── 4. cleanup that raises still fails open ──────────────────────────────────

def test_cleanup_raising_inside_the_error_path_still_fails_open(monkeypatch):
    monkeypatch.setattr(middleware, "_handle",
                        lambda kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    def broken(_route_key):
        raise RuntimeError("cleanup boom")

    monkeypatch.setattr(middleware, "_invalidate_claude_history", broken)
    out = middleware.on_llm_request(
        request={"model": "claude-opus-5"}, session_id="S", turn_id="t",
        provider="anthropic", model="claude-opus-5", api_mode="anthropic_messages")
    assert out is None
