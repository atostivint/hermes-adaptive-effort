"""Route-limited named choices and Claude's guarded per-message cache path."""

from __future__ import annotations

from conftest import import_plugin

middleware = import_plugin("middleware")

SESSION = "NATIVE-CHOICE-SESSION"
CLAUDE = {
    "provider": "anthropic",
    "model": "claude-fable-5-1",
    "api_mode": "anthropic_messages",
    "base_url": "https://api.anthropic.com",
}
BETA = "mid-conversation-output-config-2026-07-01"


class ChoiceScorer:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def classify_effort_detail(self, prompt, choices):
        self.calls.append((prompt, tuple(choices)))
        value = self.values.pop(0) if self.values else "high"
        return value, None

    def classify_detail(self, prompt):
        self.calls.append((prompt, None))
        return 1.9, None


def configure(monkeypatch, scorer=None, mode="auto", max_turns=64):
    values = {"mode": mode, "max_turns": max_turns}
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    if scorer is not None:
        monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kwargs: scorer)


def request_for(provider, model, api_mode, body, **context):
    return middleware.on_llm_request(
        request=body, session_id=SESSION, turn_id=context.pop("turn_id", "turn-1"),
        provider=provider, model=model, api_mode=api_mode, **context)


def claude_request(messages, effort="medium", beta=True, thinking="adaptive"):
    headers = {"anthropic-beta": "other-beta,another-beta",
               "x-request-option": "preserve-me"} if beta else {}
    if beta:
        headers["anthropic-beta"] = "other-beta,another-beta"
    return {
        "model": CLAUDE["model"],
        "messages": messages,
        "thinking": {"type": thinking},
        "output_config": {"effort": effort, "metadata": "preserve-me"},
        "extra_headers": headers,
    }


def claude_call(body, turn_id="turn-1", **route_overrides):
    context = dict(CLAUDE)
    context.update(route_overrides)
    return request_for(**context, body=body, turn_id=turn_id)


def test_kimi_and_openai_routes_receive_only_their_exact_native_levels(monkeypatch):
    kimi = ChoiceScorer("max")
    configure(monkeypatch, kimi)
    kimi_result = request_for("opencode-go", "kimi-k3", "chat_completions", {
        "model": "kimi-k3", "reasoning_effort": "high",
        "messages": [{"role": "user", "content": "small task"}],
    })
    assert kimi_result["request"]["reasoning_effort"] == "max"
    assert kimi.calls[0][1] == ("low", "high", "max")
    kimi_status = middleware.session_state()[f"{SESSION}/turn-1"]
    assert kimi_status["score"] is None
    assert kimi_status["decision_type"] == "native_choice"
    assert kimi_status["choices"] == ["low", "high", "max"]

    middleware.reset_state()
    openai = ChoiceScorer("xhigh")
    configure(monkeypatch, openai)
    codex_result = request_for("openai-codex", "gpt-6.1-sol", "codex_responses", {
        "model": "gpt-6.1-sol", "reasoning": {"effort": "medium", "summary": "auto"},
        "input": [{"role": "user", "content": "complex task"}],
    })
    assert codex_result["request"]["reasoning"]["effort"] == "xhigh"
    assert openai.calls[0][1] == ("low", "medium", "high", "xhigh", "max")


def test_every_named_level_is_selectable_for_two_through_five_level_routes(monkeypatch):
    cases = [
        ("opencode-go", "glm-5.2", "chat_completions", "high", ("high", "max"),
         {"reasoning_effort": "high"}),
        ("opencode-go", "kimi-k3", "chat_completions", "low", ("low", "high", "max"),
         {"reasoning_effort": "low"}),
        ("anthropic", "claude-opus-4-6", "anthropic_messages", "https://api.anthropic.com",
         ("low", "medium", "high", "max"), {"output_config": {"effort": "low"}}),
        ("openai-codex", "gpt-6.1-sol", "codex_responses", "low",
         ("low", "medium", "high", "xhigh", "max"),
         {"reasoning": {"effort": "low", "summary": "auto"}}),
        ("opencode-zen", "muse-spark-1.3-contributor-free", "codex_responses", "low",
         ("minimal", "low", "medium", "high", "xhigh"), {"reasoning": {"effort": "low"}}),
    ]
    for case_index, (provider, model, api_mode, host_or_level, choices, control) in enumerate(cases):
        for choice in choices:
            middleware.reset_state()
            scorer = ChoiceScorer(choice)
            configure(monkeypatch, scorer, mode="always")
            kwargs = {"base_url": host_or_level} if provider == "anthropic" else {}
            initial = next(level for level in choices if level != choice)
            request_control = dict(control)
            if "reasoning_effort" in request_control:
                request_control["reasoning_effort"] = initial
            elif "output_config" in request_control:
                request_control["output_config"] = {"effort": initial}
            else:
                request_control["reasoning"] = {"effort": initial, "summary": "auto"}
            body = {"model": model, **control,
                    "messages": [{"role": "user", "content": "task"}],
                    "input": [{"role": "user", "content": "task"}]}
            body.update(request_control)
            result = request_for(
                provider, model, api_mode, body, turn_id=f"turn-{case_index}-{choice}",
                **kwargs)
            assert result is not None, (provider, model, choice)
            applied = result["request"]
            target = applied.get("reasoning_effort")
            if isinstance(applied.get("reasoning"), dict):
                target = applied["reasoning"].get("effort")
            if isinstance(applied.get("output_config"), dict):
                target = applied["output_config"].get("effort")
            assert target == choice
            assert scorer.calls[0][1] == choices
            if model == "claude-opus-4-6":
                assert "xhigh" not in scorer.calls[0][1]


def test_native_choice_route_change_does_not_promote_xhigh_to_new_route_max(monkeypatch):
    scorer = ChoiceScorer("xhigh")
    configure(monkeypatch, scorer, mode="always")
    first = request_for("openai-codex", "gpt-6.1-sol", "codex_responses", {
        "model": "gpt-6.1-sol", "reasoning": {"effort": "medium"},
        "input": [{"role": "user", "content": "task"}],
    })
    assert first["request"]["reasoning"]["effort"] == "xhigh"
    next_route = request_for("opencode-go", "kimi-k3", "chat_completions", {
        "model": "kimi-k3", "reasoning_effort": "low",
        "messages": [{"role": "user", "content": "task"}],
    })
    assert next_route["request"]["reasoning_effort"] == "high"
    assert len(scorer.calls) == 1


def test_invalid_named_choice_fails_open_once_without_score_fallback(monkeypatch):
    scorer = ChoiceScorer("ultra")
    configure(monkeypatch, scorer)
    original = {
        "model": "kimi-k3", "reasoning_effort": "high",
        "messages": [{"role": "user", "content": "task"}],
    }

    assert request_for("opencode-go", "kimi-k3", "chat_completions", original) is None
    status = middleware.session_state()[f"{SESSION}/turn-1"]
    assert status["state"] == "failed"
    assert status["failure"] == "malformed_response"
    assert status["score"] is None
    assert len(scorer.calls) == 1


def test_single_level_route_is_fixed_without_calling_scorer(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    monkeypatch.setattr(middleware._effort, "route_choice_levels", lambda *_args: ("high",))
    result = request_for("opencode-go", "kimi-k3", "chat_completions", {
        "model": "kimi-k3", "reasoning_effort": "low",
        "messages": [{"role": "user", "content": "task"}],
    })

    assert result["request"]["reasoning_effort"] == "high"
    assert scorer.calls == []
    status = middleware.session_state()[f"{SESSION}/turn-1"]
    assert status["decision_type"] == "fixed"
    assert status["score"] is None
    assert status["probes"] == 0


def test_unknown_vocabularies_keep_the_legacy_score_contract(monkeypatch):
    scorer = ChoiceScorer()
    configure(monkeypatch, scorer)
    result = request_for("openrouter", "vendor/model", "chat_completions", {
        "model": "vendor/model", "extra_body": {
            "reasoning": {"enabled": True, "effort": "low"}},
        "messages": [{"role": "user", "content": "task"}],
    })

    assert result["request"]["extra_body"]["reasoning"]["effort"] == "high"
    status = middleware.session_state()[f"{SESSION}/turn-1"]
    assert status["decision_type"] == "legacy_score"
    assert status["score"] == 1.9
    assert status["choices"] == []


def test_claude_markers_replay_at_the_same_turn_boundaries_without_changing_initial_effort(
        monkeypatch):
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer)
    first = claude_request([{"role": "user", "content": "first task"}])
    first_result = claude_call(first)
    first_out = first_result["request"]
    assert first_out["output_config"] == first["output_config"]
    assert first["extra_headers"]["anthropic-beta"] == "other-beta,another-beta"
    assert first_out["extra_headers"]["anthropic-beta"] == f"other-beta,another-beta,{BETA}"
    assert first_out["extra_headers"]["x-request-option"] == "preserve-me"
    assert first_out["messages"] == [
        {"role": "system", "content": [], "output_config": {"effort": "high"}},
        {"role": "user", "content": "first task"},
    ]
    first_status = middleware.session_state()[f"{SESSION}/turn-1"]
    assert first_status["decision_type"] == "native_choice"
    assert first_status["choices"] == ["low", "medium", "high", "xhigh", "max"]
    assert first_status["target"] == "high"
    assert first_status["score"] is None
    assert first_status["cache_behavior"] == "per_message"

    tool_loop = claude_request([
        {"role": "user", "content": "first task"},
        {"role": "assistant", "content": "using a tool"},
        {"role": "user", "content": [{"type": "tool_result", "content": "result"}]},
    ])
    repeated = claude_call(tool_loop)
    assert [m for m in repeated["request"]["messages"] if m.get("role") == "system"] == [
        {"role": "system", "content": [], "output_config": {"effort": "high"}}
    ]
    assert repeated["request"]["extra_headers"]["anthropic-beta"] == \
        f"other-beta,another-beta,{BETA}"
    assert len(scorer.calls) == 1

    second = claude_request([
        {"role": "user", "content": "first task"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "second task"},
    ])
    second_result = claude_call(second, turn_id="turn-2")
    second_out = second_result["request"]
    assert second_out["output_config"] == second["output_config"]
    assert [(m.get("output_config") or {}).get("effort") for m in second_out["messages"]
            if m.get("role") == "system"] == ["high", "low"]
    assert [m["content"] for m in second_out["messages"] if m.get("role") == "user"] == [
        "first task", "second task"]
    assert scorer.calls[1][1] == ("low", "medium", "high", "xhigh", "max")


def test_claude_markers_survive_moving_prompt_cache_decoration(monkeypatch):
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer)
    marker = {"type": "ephemeral"}
    first = claude_request([{"role": "user", "content": [
        {"type": "text", "text": "first ", "cache_control": marker},
        {"type": "text", "text": "task"}]}])
    assert claude_call(first) is not None

    second = claude_request([
        {"role": "user", "content": "first task"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": [
            {"type": "text", "text": "second task", "cache_control": marker}]},
    ])
    second_out = claude_call(second, turn_id="turn-2")["request"]
    assert [(m.get("output_config") or {}).get("effort") for m in second_out["messages"]
            if m.get("role") == "system"] == ["high", "low"]
    status = middleware.session_state()[f"{SESSION}/turn-2"]
    assert status.get("failure") is None
    assert status["cache_behavior"] == "per_message"


def test_claude_auto_without_visible_beta_pins_per_route_and_always_reports_cache_risk(
        monkeypatch):
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer, mode="auto")
    first = claude_request([{"role": "user", "content": "first"}], beta=False)
    out1 = claude_call(first)["request"]
    second = claude_request([{"role": "user", "content": "second"}], beta=False)
    out2 = claude_call(second, turn_id="turn-2")["request"]
    assert out1["output_config"]["effort"] == "high"
    assert out2["output_config"]["effort"] == "high"
    assert len(scorer.calls) == 1
    assert middleware.session_state()[f"{SESSION}/turn-2"]["cache_behavior"] == "route_pinned"

    middleware.reset_state()
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer, mode="always")
    a = claude_request([{"role": "user", "content": "first"}], beta=False)
    b = claude_request([{"role": "user", "content": "second"}], beta=False)
    assert claude_call(a)["request"]["output_config"]["effort"] == "high"
    assert claude_call(b, turn_id="turn-2")["request"]["output_config"]["effort"] == "low"
    assert middleware.session_state()[f"{SESSION}/turn-2"]["cache_behavior"] == \
        "top_level_cache_may_reset"


def test_claude_incompatible_or_unverified_routes_do_not_call_scorer(monkeypatch):
    scorer = ChoiceScorer("high")
    configure(monkeypatch, scorer)
    between_tools = claude_request([{"role": "user", "content": "task"}],
                                  thinking="between_tools")
    assert claude_call(between_tools) is None
    assert scorer.calls == []

    proxy = claude_request([{"role": "user", "content": "task"}])
    assert claude_call(proxy, base_url="https://proxy.example") is None
    assert scorer.calls == []


def test_claude_compression_and_manual_initial_change_invalidate_continuity(monkeypatch):
    scorer = ChoiceScorer("high", "low", "low")
    configure(monkeypatch, scorer)
    initial = claude_request([{"role": "user", "content": "original"}])
    assert claude_call(initial) is not None

    compressed = claude_request([{"role": "user", "content": "compressed summary"}])
    assert claude_call(compressed, turn_id="turn-2") is None
    assert len(scorer.calls) == 1
    assert middleware.session_state()[f"{SESSION}/turn-2"]["failure"] == \
        "cache_continuity_invalid"

    middleware.reset_state()
    configure(monkeypatch, scorer)
    assert claude_call(initial) is not None
    changed = claude_request([
        {"role": "user", "content": "original"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "new task"},
    ], effort="low")
    assert claude_call(changed, turn_id="turn-2") is None
    assert len(scorer.calls) == 2


def test_claude_history_capacity_keeps_the_last_effort_without_another_score(monkeypatch):
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer, max_turns=1)
    first = claude_request([{"role": "user", "content": "first"}])
    assert claude_call(first) is not None
    next_turn = claude_request([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "next"},
    ])
    result = claude_call(next_turn, turn_id="turn-2")

    assert len(scorer.calls) == 1
    assert result["request"]["messages"] == [
        {"role": "system", "content": [], "output_config": {"effort": "high"}},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "next"},
    ]
    status = middleware.session_state()[f"{SESSION}/turn-2"]
    assert status["decision_type"] == "fixed"
    assert status["label"] == "high"


def test_claude_internal_error_invalidates_existing_marker_history(monkeypatch):
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer)
    initial = claude_request([{"role": "user", "content": "first"}])
    assert claude_call(initial) is not None
    route_key = middleware._pin_key(
        SESSION, middleware._route_identity("anthropic", CLAUDE["model"],
                                            "anthropic_messages"))
    monkeypatch.setattr(
        middleware, "_claude_apply_choice",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("test failure")))
    next_turn = claude_request([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "next"},
    ])

    assert claude_call(next_turn, turn_id="turn-2") is None
    assert middleware._CLAUDE_HISTORY[route_key]["invalid"] is True
