"""Per-turn effort classification for normal (non-delegated) sessions.

One decision per SESSION is the wrong granularity for an interactive session: a
first query of "hey" would freeze ``low`` onto a conversation that later asks for
a multi-region redesign. Verified against the installed source:

* ``_bind_turn_identity`` (``agent/turn_context.py:536``) mints a fresh
  ``turn_id`` per user message, so it is stable for every API request of that
  turn and different for the next one.
* A subagent is one session AND typically one turn, so per-turn granularity
  costs the same single scorer call there — it does not multiply the cost.

So the memo key moves from ``session_id`` to ``(session_id, turn_id)``: a
multi-call turn (a tool loop) reuses the decision its first call made. The turn id
is the only authority here — ``api_call_count`` is not consulted, so the reuse
holds whatever the host reports for it.
"""

from __future__ import annotations

import pytest

from conftest import import_plugin, settings_with_prompt_consent

middleware = import_plugin("middleware")


class FakeClassifier:
    def __init__(self, score=1.0):
        self.score = score
        self.timeout = None
        self.calls: list = []

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        return self.score, None


class ScoreSequence:
    """Replays a scripted score per call so ordering bugs are visible."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.calls: list = []
        self.instances = []

    def __call__(self, timeout=None):
        client = FakeClassifier()
        parent = self

        def classify_detail(prompt):
            parent.calls.append(prompt)
            return (parent.scores.pop(0) if parent.scores else 0.0), None

        client.classify_detail = classify_detail
        self.instances.append(client)
        return client


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    middleware.reset_state()
    monkeypatch.setattr(middleware, "_settings_provider", None)
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    yield
    middleware.reset_state()


def use_settings(monkeypatch, **kw):
    kw = settings_with_prompt_consent(kw)
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: kw.get(key, default))


def turn_request(text="Design a failover plan", effort="medium"):
    return {
        "model": "openrouter/x/y",
        "messages": [{"role": "user", "content": text}],
        "extra_body": {"reasoning": {"enabled": True, "effort": effort}},
    }


def ask(monkeypatch, text, turn, session="MAIN", score=1.0):
    """One user turn, one API request (the common case)."""
    seq = ScoreSequence([score])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    req = turn_request(text)
    out = middleware.on_llm_request(
        request=req, session_id=session, provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", task_id="t", turn_id=turn, api_request_id="r1",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    return out, req, seq


# ── the bug: a trivial first query must not freeze the session ───────────────

def test_second_turn_is_reclassified(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([0.1, 1.9]))

    req1 = turn_request("hey")
    out1 = middleware.on_llm_request(
        request=req1, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", task_id="t", turn_id="t1", api_request_id="r1",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    assert out1["request"]["extra_body"]["reasoning"]["effort"] == "low"

    req2 = turn_request("Redesign multi-region failover with quorum consensus")
    out2 = middleware.on_llm_request(
        request=req2, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", task_id="t", turn_id="t2", api_request_id="r2",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    assert out2 is not None
    assert out2["request"]["extra_body"]["reasoning"]["effort"] == "high"


@pytest.mark.parametrize("mode", ["auto", "cache_safe"])
@pytest.mark.parametrize("shape", ["extra_body", "reasoning_effort", "codex_input"])
def test_full_history_classifies_current_turn_and_reuses_it_in_tool_loop(monkeypatch, mode, shape):
    use_settings(monkeypatch, mode=mode)
    prompts = ["hey", "Redesign multi-region failover with quorum consensus", "thanks"]
    calls = []

    class PromptClassifier:
        def classify_detail(self, prompt):
            calls.append(prompt)
            return (1.9 if prompt == prompts[1] else 0.1), None

    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kw: PromptClassifier())
    history = []
    for index, prompt in enumerate(prompts):
        # Include content blocks, prior answers, and trailing tool results.
        history.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})
        for call_count in (1, 2):
            req = {"messages": list(history), "extra_body": {"reasoning": {"effort": "medium"}}}
            provider, model, api_mode = "openrouter", "openrouter/x/y", "chat_completions"
            if shape == "reasoning_effort":
                req.pop("extra_body")
                req["reasoning_effort"] = "medium"
                provider, model = "opencode-go", "deepseek-v4-pro"
            elif shape == "codex_input":
                req["input"] = req.pop("messages")
                req.pop("extra_body")
                req["reasoning"] = {"effort": "medium", "summary": "auto"}
                provider, model, api_mode = "openai-codex", "gpt-6.1-sol", "codex_responses"
            out = middleware.on_llm_request(
                request=req, session_id="MAIN", turn_id=f"t{index}",
                provider=provider, model=model, api_mode=api_mode, api_call_count=call_count)
            expected = "high" if index == 1 else "low"
            assert out is not None
            assert middleware._effort_slot(out["request"])[2] == expected
            assert middleware._effort_slot(req)[2] == "medium"
            if call_count == 1:
                history.extend([
                    {"role": "assistant", "content": "Checking", "tool_calls": [{"id": "call"}]},
                    {"role": "tool", "content": "tool output", "tool_call_id": "call"},
                ])
    assert calls == prompts
    assert [entry["probes"] for entry in middleware.session_state().values()] == [1, 1, 1]
    assert [event["to"] for event in middleware.effort_change_state()["events"]] == ["low", "high", "low"]


def test_same_turn_tool_loop_keeps_one_decision(monkeypatch):
    """api_call_count>1 within a turn = tool loop: one probe, not one per call."""
    use_settings(monkeypatch, mode="auto")
    seq = ScoreSequence([1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)

    for call_count in (1, 2, 3, 4):
        req = turn_request("Audit this k8s manifest")
        out = middleware.on_llm_request(
            request=req, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
            api_mode="chat_completions", task_id="t", turn_id="turn-A", api_request_id=f"r{call_count}",
            api_call_count=call_count, middleware_schema_version="hermes.middleware.v1")
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(seq.calls) == 1


def test_subagent_still_costs_one_call(monkeypatch):
    """Per-turn granularity must not multiply the cost for a subagent."""
    use_settings(monkeypatch, mode="auto", subagent_mode="auto")
    seq = ScoreSequence([1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    middleware.on_subagent_start(
        parent_session_id="P", child_session_id="C",
        child_goal="Migrate the auth service to the new token format")
    for call_count in (1, 2, 3):
        req = {"model": "gpt-5.6-terra",
               "messages": [{"role": "user", "content": "go"}],
               "reasoning": {"effort": "medium", "summary": "auto"}}
        out = middleware.on_llm_request(
            request=req, session_id="C", provider="openai-codex", model="gpt-5.6-terra",
            api_mode="codex_responses", task_id="t", turn_id="sub-turn",
            api_request_id=f"r{call_count}", api_call_count=call_count,
            middleware_schema_version="hermes.middleware.v1")
        assert out["request"]["reasoning"]["effort"] == "high"
    assert len(seq.calls) == 1


def test_turn_id_isolates_concurrent_sessions(monkeypatch):
    """Same turn_id in two sessions must not share a decision."""
    use_settings(monkeypatch, mode="auto")
    # A/t1 and B/t1 each classify (two calls, both "low"); A/t2 is a new turn.
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([0.1, 0.1, 1.9]))

    for session in ("A", "B"):
        req = turn_request("hey")
        out = middleware.on_llm_request(
            request=req, session_id=session, provider="openrouter", model="openrouter/x/y",
            api_mode="chat_completions", task_id="t", turn_id="t1", api_request_id="r",
            api_call_count=1, middleware_schema_version="hermes.middleware.v1")
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "low"

    req = turn_request("now something hard")
    out = middleware.on_llm_request(
        request=req, session_id="A", provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", task_id="t", turn_id="t2", api_request_id="r",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"


def test_missing_turn_id_still_memoises_per_session(monkeypatch):
    """Without a turn id the key degrades to session-only; never a crash."""
    use_settings(monkeypatch, mode="auto")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)

    for _ in range(2):
        req = turn_request("hey")
        out = middleware.on_llm_request(
            request=req, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
            api_mode="chat_completions", task_id="t", turn_id="", api_request_id="r",
            api_call_count=1, middleware_schema_version="hermes.middleware.v1")
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert len(seq.calls) == 1


def test_state_key_carries_the_turn(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([0.1]))
    req = turn_request("hey")
    middleware.on_llm_request(
        request=req, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", task_id="t", turn_id="turn-X", api_request_id="r",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    assert "MAIN/turn-X" in middleware.session_state()


def test_turn_bound_evicts_oldest_turn(monkeypatch):
    use_settings(monkeypatch, mode="auto", max_turns=2)
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([1.9] * 6))
    for index in range(4):
        req = turn_request(f"task {index}")
        middleware.on_llm_request(
            request=req, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
            api_mode="chat_completions", task_id="t", turn_id=f"t{index}", api_request_id="r",
            api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    keys = list(middleware.session_state())
    assert len(keys) == 2
    assert keys == ["MAIN/t2", "MAIN/t3"]


def test_turn_bound_does_not_count_subagent_registry(monkeypatch):
    """The child registry has its own FIFO, independent of the decision bound.

    They share one cap value (``max_turns``) but not one eviction list, so a
    burst of subagents cannot evict live decisions, and old decisions cannot
    unregister a running child.
    """
    use_settings(monkeypatch, mode="auto", subagent_mode="auto", max_turns=5)
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([1.9] * 8))
    for index in range(3):
        middleware.on_subagent_start(
            parent_session_id="P", child_session_id=f"c{index}", child_goal=f"goal {index}")
    for index in range(3):
        req = {"model": "gpt-5.6-terra", "messages": [{"role": "user", "content": "go"}],
               "reasoning": {"effort": "medium", "summary": "auto"}}
        middleware.on_llm_request(
            request=req, session_id="MAIN", provider="openrouter", model="openrouter/x/y",
            api_mode="chat_completions", task_id="t", turn_id=f"t{index}", api_request_id="r",
            api_call_count=1, middleware_schema_version="hermes.middleware.v1")
    # 3 decisions under the cap, 3 children still registered: no cross-eviction.
    assert len(middleware.session_state()) == 3
    assert len(middleware.child_goals()) == 3
