"""Local escalation on repeated identical tool errors (issue #34); fakes only, network-free."""

from __future__ import annotations

import hashlib
import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")
tool_errors = import_plugin("tool_errors")
effort = import_plugin("effort")
command = import_plugin("command")

SESSION = "ESC-SESSION"
CODEX = ("openai-codex", "gpt-6.1-sol", "codex_responses")
GO_CHAT = ("opencode-go", "glm-5.3", "chat_completions")
PYTEST_FAIL = ("\x1b[31mFAILED\x1b[0m tests/test_a.py::test_x - AssertionError: assert 3 == 4\n"
               "tests/test_a.py:41: AssertionError\n=== 1 failed in 0.12s ===")


class ChoiceScorer:
    def __init__(self, value="medium"):
        self.value, self.calls = value, []

    def classify_effort_detail(self, prompt, choices):
        self.calls.append(tuple(choices))
        return self.value, None

    def classify_detail(self, prompt):
        self.calls.append(None)
        return 1.0, None


def configure(monkeypatch, scorer=None, **values):
    values.setdefault("mode", "always")
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    if scorer is not None:
        monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kwargs: scorer)


def terminal_error(text=PYTEST_FAIL, code=1):
    return json.dumps({"output": text, "exit_code": code, "error": None})


def edit_ok():
    return json.dumps({"success": True, "diff": "...", "files_modified": ["a.py"]})


def chat_messages(*steps):
    """steps: ('run', result) or ('edit', result) or ('read', result) tool calls, in order."""
    messages = [{"role": "user", "content": "fix the failing test"}]
    for index, (kind, result) in enumerate(steps):
        name = {"run": "terminal", "edit": "patch", "write": "write_file", "read": "read_file"}[kind]
        call_id = f"c{index}"
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
    return messages


def responses_input(*steps):
    items = [{"role": "user", "content": "fix the failing test"}]
    for index, (kind, result) in enumerate(steps):
        name = {"run": "terminal", "edit": "patch", "write": "write_file", "read": "read_file"}[kind]
        items.append({"type": "function_call", "call_id": f"c{index}", "name": name,
                      "arguments": "{}"})
        items.append({"type": "function_call_output", "call_id": f"c{index}", "output": result})
    return items


def send(steps, route=CODEX, turn="t1", effort_value="low", shape=None):
    provider, model, api_mode = route
    if api_mode == "codex_responses":
        request = {"model": model, "input": responses_input(*steps),
                   "reasoning": {"effort": effort_value, "summary": "auto"}}
    else:
        request = {"model": model, "messages": chat_messages(*steps),
                   "reasoning_effort": effort_value}
    out = middleware.on_llm_request(
        request=request, session_id=SESSION, turn_id=turn, provider=provider, model=model,
        api_mode=api_mode)
    if out is None:
        return None
    sent = out["request"]
    return (sent.get("reasoning") or {}).get("effort") or sent.get("reasoning_effort")


@pytest.fixture(autouse=True)
def clean():
    middleware.reset_state()
    yield
    middleware.reset_state()


REPEAT_WITH_EDIT = [("run", terminal_error()), ("edit", edit_ok()), ("run", terminal_error())]


# ── the issue's five cases ───────────────────────────────────────────────────

def test_same_error_twice_with_an_edit_between_raises_one_level(monkeypatch):
    scorer = ChoiceScorer("medium")
    configure(monkeypatch, scorer)
    assert send(REPEAT_WITH_EDIT[:1]) == "medium"
    assert send(REPEAT_WITH_EDIT) == "high"
    assert len(scorer.calls) == 1  # local signal: no second scorer call
    entry = middleware.session_state()[f"{SESSION}/t1"]
    assert entry["escalated"] is True and entry["escalation_reason"] == "repeated_tool_error"
    assert entry["label"] == "medium" and entry["state"] == "decided"


def test_same_error_twice_without_an_edit_changes_nothing(monkeypatch):
    configure(monkeypatch, ChoiceScorer("medium"))
    steps = [("run", terminal_error()), ("read", json.dumps({"content": "x"})),
             ("run", terminal_error())]
    assert send(steps) == "medium"
    assert middleware.session_state()[f"{SESSION}/t1"]["escalated"] is False


def test_different_errors_change_nothing(monkeypatch):
    configure(monkeypatch, ChoiceScorer("medium"))
    steps = [("run", terminal_error("AssertionError: assert 3 == 4")), ("edit", edit_ok()),
             ("run", terminal_error("ImportError: cannot import name x"))]
    assert send(steps) == "medium"
    assert middleware.session_state()[f"{SESSION}/t1"]["escalated"] is False


@pytest.mark.parametrize("route", [
    ("openrouter", "x/y", "chat_completions"),
    ("openai-codex", "gpt-5.5", "codex_responses"),
])
def test_non_dynamic_route_changes_nothing(monkeypatch, route):
    assert not middleware._dynamic_effort_route(*route)
    def run(steps, session):
        middleware.reset_state()
        configure(monkeypatch, ChoiceScorer("low"))
        request = {"model": route[1], "messages": chat_messages(*steps),
                   "reasoning_effort": "high"}
        out = middleware.on_llm_request(
            request=request, session_id=session, turn_id="t1", provider=route[0],
            model=route[1], api_mode=route[2])
        states = [e["escalated"] for e in middleware.session_state().values()]
        return (out["request"]["reasoning_effort"] if out else None), states

    baseline = run([("run", terminal_error())], SESSION)
    repeated = run(REPEAT_WITH_EDIT, SESSION)
    assert repeated == baseline and not any(repeated[1])


def test_top_of_the_vocabulary_changes_nothing(monkeypatch):
    configure(monkeypatch, ChoiceScorer("max"))
    assert send(REPEAT_WITH_EDIT) == "max"
    assert middleware.session_state()[f"{SESSION}/t1"]["escalated"] is False
    assert middleware.effort_change_state()["events"][-1]["to"] == "max"


# ── behaviour details ────────────────────────────────────────────────────────

def test_holds_for_the_rest_of_the_turn_and_resets_next_turn(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    # The host re-sends the same configured effort on every request of a conversation.
    assert send(REPEAT_WITH_EDIT, effort_value="high") == "medium"
    later = REPEAT_WITH_EDIT + [("read", json.dumps({"content": "ok"}))]
    assert send(later, effort_value="high") == "medium"  # no new error: still raised
    assert send(later + [("run", json.dumps({"output": "ok", "exit_code": 0, "error": None}))],
                effort_value="high") == "medium"
    assert send([("run", json.dumps({"output": "ok", "exit_code": 0}))], turn="t2",
                effort_value="high") == "low"
    assert len(scorer.calls) == 2  # one per user turn


def test_one_escalation_per_fingerprint_and_a_new_one_adds_a_level(monkeypatch):
    configure(monkeypatch, ChoiceScorer("low"))
    third_time = REPEAT_WITH_EDIT + [("edit", edit_ok()), ("run", terminal_error())]
    assert send(REPEAT_WITH_EDIT) == "medium"
    assert send(third_time) == "medium"  # same fingerprint: not again
    other = third_time + [("run", terminal_error("TypeError: bad operand")), ("edit", edit_ok()),
                          ("run", terminal_error("TypeError: bad operand"))]
    assert send(other) == "high"  # a different repeated error: one more level


def test_never_exceeds_the_route_top_and_never_reaches_ultra_or_none(monkeypatch):
    assert effort.raise_choice_level("xhigh", ("low", "xhigh", "ultra"), 5) == "xhigh"
    assert effort.raise_choice_level("max", ("low", "high", "max")) == "max"
    assert effort.raise_choice_level("low", ("low", "high", "max"), 1) == "high"
    assert effort.raise_choice_level("medium", ("low", "high", "max")) == "medium"
    # ``none`` is the lowest level escalation can start from (issue #83): it is raised by
    # one step, but no raise ever *reaches* ``none`` from above.
    assert effort.raise_choice_level("none", ("none", "low")) == "low"
    assert effort.raise_choice_level("none", ("none", "low", "high"), 5) == "high"
    assert effort.raise_choice_level("low", ("none", "low", "high"), 1) == "high"
    assert effort.raise_choice_level("none", ("low", "high")) == "none"


def test_works_on_chat_completions_dynamic_route_with_the_route_vocabulary(monkeypatch):
    assert middleware._dynamic_effort_route(*GO_CHAT)
    configure(monkeypatch, ChoiceScorer("low"))
    assert send(REPEAT_WITH_EDIT, route=GO_CHAT, effort_value="low") == "high"  # low/high/max


@pytest.mark.parametrize("mode", ["auto", "once", "always"])
def test_every_active_mode_on_a_dynamic_route(monkeypatch, mode):
    configure(monkeypatch, ChoiceScorer("low"), mode=mode)
    assert send(REPEAT_WITH_EDIT, effort_value="high") == "medium"
    if mode == "once":  # the retained route decision keeps its own level for later turns
        assert send([("run", edit_ok())], turn="t2", effort_value="high") == "low"


def test_write_file_counts_as_an_edit_attempt(monkeypatch):
    configure(monkeypatch, ChoiceScorer("low"))
    steps = [("run", terminal_error()), ("write", json.dumps({"error": "denied"})),
             ("run", terminal_error())]
    assert send(steps) == "medium"


def test_setting_false_is_identical_to_master_behaviour(monkeypatch):
    configure(monkeypatch, ChoiceScorer("medium"), tool_error_escalation=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("fingerprinting must not run when the setting is false")

    monkeypatch.setattr(tool_errors, "repeated_error_fingerprints", boom)
    request = {"model": CODEX[1], "input": responses_input(*REPEAT_WITH_EDIT),
               "reasoning": {"effort": "low", "summary": "auto"}}
    out = middleware.on_llm_request(
        request=request, session_id=SESSION, turn_id="t1", provider=CODEX[0], model=CODEX[1],
        api_mode=CODEX[2])
    expected = dict(request)
    expected["reasoning"] = {"effort": "medium", "summary": "auto"}
    assert out == {"request": expected, "source": middleware.PLUGIN_ID,
                   "reason": "always low -> medium (medium)"}
    entry = middleware.session_state()[f"{SESSION}/t1"]
    assert entry["escalated"] is False and "_escalation" not in entry
    assert [e["to"] for e in middleware.effort_change_state()["events"]] == ["medium"]


def test_default_is_on():
    assert middleware.DEFAULTS["tool_error_escalation"] is True
    assert middleware._settings()["tool_error_escalation"] is True


# ── normalisation ────────────────────────────────────────────────────────────

def test_normalisation_ignores_volatile_tokens_but_not_messages():
    def fp(text):
        return tool_errors.fingerprint(text)

    base = fp(PYTEST_FAIL)
    assert base == fp(PYTEST_FAIL.replace("\x1b[31m", "").replace("\x1b[0m", ""))
    assert base == fp(PYTEST_FAIL.replace("\n", "\r\n"))
    assert base == fp(PYTEST_FAIL.replace(":41:", ":97:").replace("0.12s", "13.40s"))
    assert fp("File \"x.py\", line 12, in f\nValueError: bad") == \
        fp("File \"x.py\", line 99, in f\nValueError: bad")
    assert fp("took 123 ms at 0x7f3a9c001234 in /tmp/pytest-of-u/pytest-3/t0/a.py") == \
        fp("took 7 ms at 0xdeadbeef in /tmp/pytest-of-u/pytest-9/t5/a.py")
    assert fp("src/a.rs:12:5: error[E0308]: mismatched types") == \
        fp("src/a.rs:40:9: error[E0308]: mismatched types")
    assert base != fp(PYTEST_FAIL.replace("3 == 4", "5 == 6"))  # values are kept
    assert fp("   \x1b[0m  ") is None and fp("") is None and fp(None) is None


def test_fingerprint_is_a_capped_sha256_of_the_normalised_tail():
    long_text = "head-" + "x" * 5000 + "TAIL"
    digest = tool_errors.fingerprint(long_text)
    assert digest == hashlib.sha256(long_text[-2000:].encode()).hexdigest()
    assert len(digest) == 64


def test_result_shapes_only_real_errors_count():
    err = tool_errors.result_error_text
    assert err(json.dumps({"error": "boom"})) == "boom"
    assert err(json.dumps({"output": "FAILED x", "exit_code": 2, "error": None})) == "FAILED x"
    assert err(json.dumps({"output": "", "exit_code": 3})) == "exit 3"
    assert err(json.dumps({"output": "ok", "exit_code": 0, "error": None})) is None
    assert err(json.dumps({"success": True, "diff": "x"})) is None
    assert err(json.dumps({"error": "repeated read", "guardrail_refusal": True})) is None
    assert err("plain text Error: not json") is None
    assert err([{"type": "input_text", "text": json.dumps({"error": "boom"})}]) == "boom"
    assert err("denied", flagged=True) == "denied"  # Anthropic tool_result.is_error


def test_anthropic_shaped_requests_are_scanned_too():
    def call(name, tool_id):
        return {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": {}}]}

    def result(tool_id, text, error):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": error}]}

    messages = [{"role": "user", "content": "go"}, call("terminal", "a"), result("a", "boom 1", True),
                call("patch", "b"), result("b", "ok", False), call("terminal", "c"),
                result("c", "boom 1", True)]
    assert len(tool_errors.repeated_error_fingerprints({"messages": messages})) == 1
    no_edit = [m for m in messages if m is not messages[3] and m is not messages[4]]
    assert tool_errors.repeated_error_fingerprints({"messages": no_edit}) == []


def test_lookback_is_the_last_twenty_tool_results():
    filler = [("read", json.dumps({"content": "x"}))] * 19
    inside = REPEAT_WITH_EDIT[:2] + filler[:17] + REPEAT_WITH_EDIT[2:]  # 20 results
    outside = REPEAT_WITH_EDIT[:2] + filler[:18] + REPEAT_WITH_EDIT[2:]  # 21 results
    assert len(tool_errors.repeated_error_fingerprints(
        {"input": responses_input(*inside)})) == 1
    assert tool_errors.repeated_error_fingerprints(
        {"input": responses_input(*outside)}) == []


def test_only_the_current_user_turn_is_scanned():
    items = responses_input(*REPEAT_WITH_EDIT) + [{"role": "user", "content": "next task"}]
    assert tool_errors.repeated_error_fingerprints({"input": items}) == []


# ── feed, status, privacy, Claude ────────────────────────────────────────────

def test_escalation_is_recorded_once_in_the_applied_change_feed(monkeypatch):
    configure(monkeypatch, ChoiceScorer("low"))
    send(REPEAT_WITH_EDIT[:1], effort_value="low")
    assert send(REPEAT_WITH_EDIT) == "medium"
    assert send(REPEAT_WITH_EDIT + [("read", json.dumps({"content": "x"}))]) == "medium"
    events = middleware.effort_change_state()["events"]
    assert [(e["from"], e["to"]) for e in events] == [("low", "medium")]
    assert set(events[0]) == {"id", "from", "to", "at"}


def test_status_fields_are_additive_and_text_free(monkeypatch):
    configure(monkeypatch, ChoiceScorer("low"))
    send(REPEAT_WITH_EDIT)
    payload = json.loads(command.handle("status json"))
    assert payload["schema"] == "hermes-adaptive-effort.status.v2"
    assert payload["settings"]["tool_error_escalation"] is True
    rendered = json.dumps(payload) + command.handle("status") + json.dumps(
        middleware.effort_change_state())
    entry = payload["sessions"][-1] if "sessions" in payload else {}
    assert entry.get("escalated") is True and entry.get("escalation_reason") == "repeated_tool_error"
    for secret in ("AssertionError", "test_x", PYTEST_FAIL[:20],
                   tool_errors.fingerprint(PYTEST_FAIL)):
        assert secret not in rendered


def test_error_text_is_never_stored_logged_or_published(monkeypatch, caplog):
    import hermes_cli.plugin_events as plugin_events

    broadcast = []
    monkeypatch.setattr(plugin_events, "broadcast_plugin_event",
                        lambda plugin, event, payload: broadcast.append(payload))
    caplog.set_level("DEBUG")
    configure(monkeypatch, ChoiceScorer("low"))
    send(REPEAT_WITH_EDIT)
    digest = tool_errors.fingerprint(PYTEST_FAIL)
    public = [json.dumps(broadcast, default=str), caplog.text,
              json.dumps(middleware.effort_change_state()), command.handle("status"),
              command.handle("status json"), command.handle("")]
    for blob in public:
        assert "AssertionError" not in blob and "test_x" not in blob and digest not in blob
    assert broadcast[-1]["status"]["escalated"] is True
    assert broadcast[-1]["applied"]["to"] == "medium"
    # Internally only the digest is kept: never the text.
    with middleware._lock:
        kept = json.dumps({k: v.get("_escalation") for k, v in middleware._SESSIONS.items()})
    assert digest in kept and "AssertionError" not in kept and "test_x" not in kept


@pytest.mark.parametrize("mode", ["auto", "once", "always"])
def test_claude_routes_are_unaffected(monkeypatch, mode):
    from test_native_choice_middleware import CLAUDE, claude_request, request_for

    configure(monkeypatch, ChoiceScorer("medium"), mode=mode)
    messages = [{"role": "user", "content": "fix it"}]
    for index, (kind, text) in enumerate(
            [("terminal", "boom"), ("patch", "ok"), ("terminal", "boom")]):
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{index}", "name": kind, "input": {}}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{index}", "content": text,
             "is_error": text == "boom"}]})
    body = claude_request(messages, effort="medium")
    assert middleware._dynamic_effort_route(
        CLAUDE["provider"], CLAUDE["model"], CLAUDE["api_mode"], request=body,
        base_url=CLAUDE["base_url"])
    assert tool_errors.repeated_error_fingerprints(body)  # the signal is present
    out = request_for(CLAUDE["provider"], CLAUDE["model"], CLAUDE["api_mode"], body,
                      base_url=CLAUDE["base_url"], turn_id="claude-turn")
    sent = out["request"] if out else body
    assert sent["output_config"]["effort"] == "medium"
    assert not any(e.get("escalated") for e in middleware.session_state().values())
    assert all(e["to"] != "high" for e in middleware.effort_change_state()["events"])
