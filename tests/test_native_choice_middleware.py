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
OPENROUTER = {
    "provider": "openrouter",
    "model": "anthropic/claude-opus-5.5",
    "api_mode": "chat_completions",
    "base_url": "https://openrouter.ai",
}
OPENROUTER_ARGON = {
    "provider": "openrouter",
    "model": "google/gemini-4-argon",
    "api_mode": "chat_completions",
    "base_url": "https://openrouter.ai",
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


def openrouter_call(body, turn_id="turn-1", route=None):
    context = dict(route or OPENROUTER)
    return request_for(**context, body=body, turn_id=turn_id)


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
        ("opencode-go", "mimo-v2.6-flash", "chat_completions", "low",
         ("low", "medium", "high"), {"reasoning_effort": "low"}),
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


def test_mimo_go_missing_field_uses_exact_chat_choice_route(monkeypatch):
    scorer = ChoiceScorer("medium")
    configure(monkeypatch, scorer)
    original = {
        "model": "mimo-v2.6-flash",
        "messages": [{"role": "user", "content": "Plan this migration"}],
    }

    result = request_for("opencode-go", "mimo-v2.6-flash", "chat_completions", original)

    assert result["request"]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in original
    assert scorer.calls[0][1] == ("low", "medium", "high")
    entry = middleware.session_state()[f"{SESSION}/turn-1"]
    assert (entry["decision_type"], entry["score"], entry["target"]) == (
        "native_choice", None, "medium")
    assert middleware._effort.route_choice_levels(
        "opencode-go", "mimo-v2.6-pro", "chat_completions") == ()
    assert middleware._effort.route_choice_levels(
        "opencode-go", "mimo-v2.6-flash", "codex_responses") == ()


def test_openrouter_claude_verbosity_uses_only_its_named_choices(monkeypatch):
    choices = ("low", "medium", "high", "xhigh", "max")
    for index, choice in enumerate(choices):
        middleware.reset_state()
        scorer = ChoiceScorer(choice)
        configure(monkeypatch, scorer, mode="always")
        initial = "medium" if choice != "medium" else "low"
        body = {
            "model": OPENROUTER["model"],
            "verbosity": initial,
            "temperature": 0.2,
            "extra_body": {"metadata": {"keep": True}},
            "messages": [{"role": "user", "content": "task"}],
        }
        result = openrouter_call(body, turn_id=f"turn-{index}")

        assert result is not None
        assert result["request"]["verbosity"] == choice
        assert result["request"]["temperature"] == 0.2
        assert result["request"]["extra_body"] == {"metadata": {"keep": True}}
        assert body["verbosity"] == initial
        assert scorer.calls[0][1] == choices
        entry = middleware.session_state()[f"{SESSION}/turn-{index}"]
        assert (entry["decision_type"], entry["score"], entry["target"]) == (
            "native_choice", None, choice)


def test_openrouter_verbosity_is_not_rewritten_on_inexact_routes(monkeypatch):
    routes = [
        dict(OPENROUTER, base_url="https://proxy.example"),
        dict(OPENROUTER, base_url="http://openrouter.ai"),
        dict(OPENROUTER, base_url="https://openrouter.ai:8443"),
        dict(OPENROUTER, base_url="https://user@openrouter.ai"),
        dict(OPENROUTER, model="claude-opus-5.5"),
        dict(OPENROUTER, model="anthropic/claude-opus-5.5:free"),
        dict(OPENROUTER, model="anthropic/claude-opus-4.8"),
        dict(OPENROUTER, model="openai/gpt-6.1"),
        dict(OPENROUTER, api_mode="anthropic_messages"),
        dict(OPENROUTER, provider="openrouter-proxy"),
    ]
    for index, route in enumerate(routes):
        middleware.reset_state()
        scorer = ChoiceScorer("high")
        configure(monkeypatch, scorer)
        result = openrouter_call({
            "model": route["model"], "verbosity": "low",
            "messages": [{"role": "user", "content": "task"}],
        }, turn_id=f"turn-{index}", route=route)

        assert result is None, route
        assert scorer.calls == [], route


def test_openrouter_verbosity_missing_malformed_disabled_or_conflicting_is_unsupported(
        monkeypatch):
    bodies = [
        {"messages": [{"role": "user", "content": "task"}]},
        {"verbosity": 7, "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "", "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "none", "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "ultra", "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "banana", "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "high", "reasoning_effort": "low",
         "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "high", "extra_body": {"reasoning": {"effort": "low"}},
         "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "high", "reasoning": {"effort": "low"},
         "messages": [{"role": "user", "content": "task"}]},
        {"verbosity": "high", "output_config": {"effort": "low"},
         "messages": [{"role": "user", "content": "task"}]},
    ]
    for index, body in enumerate(bodies):
        middleware.reset_state()
        scorer = ChoiceScorer("high")
        configure(monkeypatch, scorer)
        body = {"model": OPENROUTER["model"], **body}
        original = dict(body)
        result = openrouter_call(body, turn_id=f"turn-{index}")

        assert result is None
        assert scorer.calls == []
        assert body == original


def test_openrouter_argon_uses_the_existing_reasoning_effort_carrier(monkeypatch):
    scorer = ChoiceScorer("medium")
    configure(monkeypatch, scorer)
    body = {
        "model": OPENROUTER_ARGON["model"],
        "extra_body": {"reasoning": {"effort": "low", "enabled": True,
                                     "summary": "auto"}},
        "messages": [{"role": "user", "content": "task"}],
    }
    result = openrouter_call(body, route=OPENROUTER_ARGON)

    assert result["request"]["extra_body"]["reasoning"] == {
        "effort": "medium", "enabled": True, "summary": "auto"}
    assert body["extra_body"]["reasoning"]["effort"] == "low"
    assert scorer.calls[0][1] == ("low", "medium", "high")
    entry = middleware.session_state()[f"{SESSION}/turn-1"]
    assert (entry["decision_type"], entry["target"]) == ("native_choice", "medium")


def test_openrouter_auto_keeps_one_decision_per_route(monkeypatch):
    scorer = ChoiceScorer("high")
    configure(monkeypatch, scorer, mode="auto")
    first = openrouter_call({
        "model": OPENROUTER["model"], "verbosity": "medium",
        "messages": [{"role": "user", "content": "first"}],
    })
    second = openrouter_call({
        "model": OPENROUTER["model"], "verbosity": "medium",
        "messages": [{"role": "user", "content": "second"}],
    }, turn_id="turn-2")
    tool_loop = openrouter_call({
        "model": OPENROUTER["model"], "verbosity": "medium",
        "messages": [{"role": "user", "content": "second"}],
    })

    assert first["request"]["verbosity"] == "high"
    assert second["request"]["verbosity"] == "high"
    assert tool_loop["request"]["verbosity"] == "high"
    assert len(scorer.calls) == 1
    assert middleware.session_state()[f"{SESSION}/turn-2"]["label"] == "high"


def test_openrouter_verbosity_participates_in_manual_stand_down(monkeypatch):
    scorer = ChoiceScorer("high")
    configure(monkeypatch, scorer, mode="auto")
    first = openrouter_call({
        "model": OPENROUTER["model"], "verbosity": "medium",
        "messages": [{"role": "user", "content": "first"}],
    })
    changed = {
        "model": OPENROUTER["model"], "verbosity": "low",
        "messages": [{"role": "user", "content": "second"}],
    }
    assert first["request"]["verbosity"] == "high"
    assert openrouter_call(changed, turn_id="turn-2") is None
    assert changed["verbosity"] == "low"
    assert len(scorer.calls) == 1
    entry = middleware.session_state()[f"{SESSION}/turn-2"]
    assert entry["manual_effort"] is True


def test_openrouter_route_change_reclamps_xhigh_to_argon_high(monkeypatch):
    scorer = ChoiceScorer("xhigh")
    configure(monkeypatch, scorer, mode="always")
    first = openrouter_call({
        "model": OPENROUTER["model"], "verbosity": "medium",
        "messages": [{"role": "user", "content": "task"}],
    })
    second = openrouter_call({
        "model": OPENROUTER_ARGON["model"],
        "extra_body": {"reasoning": {"effort": "medium"}},
        "messages": [{"role": "user", "content": "task"}],
    }, route=OPENROUTER_ARGON)

    assert first["request"]["verbosity"] == "xhigh"
    assert second["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(scorer.calls) == 1


def test_openrouter_exact_routes_never_inject_a_missing_carrier(monkeypatch):
    values = {
        "mode": "auto",
        "max_turns": 64,
        "effort_models": ("anthropic/claude-opus-5.5,google/gemini-4-argon"),
    }
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: values.get(key, default))
    scorer = ChoiceScorer("high")
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kwargs: scorer)

    claude_body = {
        "model": OPENROUTER["model"],
        "messages": [{"role": "user", "content": "task"}],
    }
    argon_body = {
        "model": OPENROUTER_ARGON["model"],
        "messages": [{"role": "user", "content": "task"}],
    }
    assert openrouter_call(claude_body) is None
    assert openrouter_call(argon_body, route=OPENROUTER_ARGON) is None
    assert scorer.calls == []
    assert "verbosity" not in claude_body
    assert "extra_body" not in argon_body


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


GEMINI_ARGON = {
    "provider": "gemini",
    "model": "gemini-4-argon",
    "api_mode": "chat_completions",
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
}


def gemini_body(config=None, **extra):
    body = {
        "model": GEMINI_ARGON["model"],
        "extra_body": {"thinking_config": config if config is not None
                       else {"thinkingLevel": "low"}, **extra},
        "messages": [{"role": "user", "content": "task"}],
    }
    return body


def gemini_call(body, turn_id="turn-1", route=None):
    return request_for(**dict(route or GEMINI_ARGON), body=body, turn_id=turn_id)


def test_native_gemini_argon_rewrites_every_level_without_touching_siblings(monkeypatch):
    for index, level in enumerate(("low", "medium", "high")):
        scorer = ChoiceScorer(level)
        configure(monkeypatch, scorer, mode="always")
        body = gemini_body({"thinkingLevel": "medium" if level != "medium" else "low",
                            "includeThoughts": True}, other={"keep": 1})
        original = {**body, "extra_body": {**body["extra_body"],
                                           "thinking_config": dict(
                                               body["extra_body"]["thinking_config"])}}
        result = gemini_call(body, turn_id=f"turn-{index}")

        assert result["request"]["extra_body"]["thinking_config"] == {
            "thinkingLevel": level, "includeThoughts": True}
        assert result["request"]["extra_body"]["other"] == {"keep": 1}
        assert body == original  # the caller's request is never mutated
        assert scorer.calls[0][1] == ("low", "medium", "high")
        entry = middleware.session_state()[f"{SESSION}/turn-{index}"]
        assert (entry["decision_type"], entry["target"], entry["score"]) == (
            "native_choice", level, None)
        middleware.reset_state()


def test_native_gemini_argon_preserves_snake_case_key_style(monkeypatch):
    configure(monkeypatch, ChoiceScorer("high"), mode="always")
    result = gemini_call(gemini_body({"thinking_level": "low", "include_thoughts": True}))
    assert result["request"]["extra_body"]["thinking_config"] == {
        "thinking_level": "high", "include_thoughts": True}


def test_native_gemini_argon_accepts_provider_alias_and_prefixed_model(monkeypatch):
    configure(monkeypatch, ChoiceScorer("medium"), mode="always")
    route = dict(GEMINI_ARGON, provider="google-ai-studio", model="google/gemini-4-argon")
    result = gemini_call(gemini_body(), route=route)
    assert result["request"]["extra_body"]["thinking_config"]["thinkingLevel"] == "medium"


GEMINI3_ROUTES = {
    "gemini-3.8-flash": ("low", "medium", "high"),
    "gemini-3.6-flash": ("low", "medium", "high"),
    "gemini-3.5-flash-lite": ("low", "medium", "high"),
    "gemini-3.1-flash-lite": ("low", "medium", "high"),
    "gemini-3-flash-preview": ("low", "medium", "high"),
    "gemini-3.1-pro-preview": ("low", "high"),
}


def test_native_gemini3_routes_rewrite_only_their_exact_levels(monkeypatch):
    for model, levels in GEMINI3_ROUTES.items():
        for index, level in enumerate(levels):
            scorer = ChoiceScorer(level)
            configure(monkeypatch, scorer, mode="always")
            current = "low" if level != "low" else "high"
            body = gemini_body({"thinkingLevel": current, "includeThoughts": True})
            result = gemini_call(
                body, turn_id=f"{model}-{index}",
                route=dict(GEMINI_ARGON, model=model))
            assert result["request"]["extra_body"]["thinking_config"] == {
                "thinkingLevel": level, "includeThoughts": True}
            assert scorer.calls[0][1] == levels, model
            middleware.reset_state()


def test_native_gemini3_pro_rejects_medium_outside_its_vocabulary(monkeypatch):
    scorer = ChoiceScorer("medium")
    configure(monkeypatch, scorer, mode="always")
    body = gemini_body({"thinkingLevel": "low"})
    route = dict(GEMINI_ARGON, model="gemini-3.1-pro-preview")
    assert gemini_call(body, route=route) is None
    assert body["extra_body"]["thinking_config"] == {"thinkingLevel": "low"}
    assert scorer.calls[0][1] == ("low", "high")


def test_native_gemini_argon_unusable_controls_are_unsupported_and_unscored(monkeypatch):
    cases = [
        gemini_body({}),                                          # missing level: never injected
        {"model": "gemini-4-argon", "messages": [{"role": "user", "content": "t"}]},
        gemini_body({"thinkingLevel": "minimal"}),                # out of the route vocabulary
        gemini_body({"thinkingLevel": "xhigh"}),
        gemini_body({"thinkingLevel": 3}),
        gemini_body({"thinkingLevel": "low", "includeThoughts": False}),
        gemini_body({"thinkingLevel": "low", "thinkingBudget": 0}),
        gemini_body({"thinkingLevel": "low", "thinkingBudget": 2048}),
        gemini_body({"thinkingLevel": "low", "thinking_level": "high"}),
        gemini_body("low"),                                       # malformed thinking_config
        gemini_body(["thinkingLevel"]),
        gemini_body(None, reasoning={"effort": "low"}),           # competing carrier
    ]
    cases[-1]["extra_body"]["thinking_config"] = {"thinkingLevel": "low"}
    cases.append({**gemini_body(), "reasoning_effort": "low"})
    cases.append({**gemini_body(), "verbosity": "low"})
    cases.append({**gemini_body(), "output_config": {"effort": "low"}})
    for index, body in enumerate(cases):
        scorer = ChoiceScorer("high")
        configure(monkeypatch, scorer, mode="always")
        snapshot = repr(body)
        assert gemini_call(body, turn_id=f"turn-{index}") is None, index
        assert scorer.calls == [], index
        assert repr(body) == snapshot, index
        middleware.reset_state()


def test_native_gemini_argon_makes_no_change_on_wrong_route(monkeypatch):
    wrong = [
        dict(GEMINI_ARGON, provider="openai"),
        dict(GEMINI_ARGON, model="gemini-4-flash"),
        dict(GEMINI_ARGON, api_mode="codex_responses"),
        dict(GEMINI_ARGON, base_url="https://proxy.example/v1beta"),
        dict(GEMINI_ARGON, base_url="https://generativelanguage.googleapis.com/v1beta/openai"),
    ]
    for index, route in enumerate(wrong):
        scorer = ChoiceScorer("high")
        configure(monkeypatch, scorer, mode="always")
        body = gemini_body()
        assert gemini_call(body, turn_id=f"turn-{index}", route=route) is None, route
        assert scorer.calls == [], route
        assert body["extra_body"]["thinking_config"] == {"thinkingLevel": "low"}
        middleware.reset_state()


def test_native_gemini_argon_auto_retains_and_always_classifies_each_turn(monkeypatch):
    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer, mode="auto")
    first = gemini_call(gemini_body({"thinkingLevel": "medium"}))
    second = gemini_call(gemini_body({"thinkingLevel": "medium"}), turn_id="turn-2")
    tool_loop = gemini_call(gemini_body({"thinkingLevel": "medium"}), turn_id="turn-2")
    assert [r["request"]["extra_body"]["thinking_config"]["thinkingLevel"]
            for r in (first, second, tool_loop)] == ["high", "high", "high"]
    assert len(scorer.calls) == 1
    middleware.reset_state()

    scorer = ChoiceScorer("high", "low")
    configure(monkeypatch, scorer, mode="always")
    one = gemini_call(gemini_body({"thinkingLevel": "medium"}))
    loop = gemini_call(gemini_body({"thinkingLevel": "medium"}))
    two = gemini_call(gemini_body({"thinkingLevel": "medium"}), turn_id="turn-2")
    assert one["request"]["extra_body"]["thinking_config"]["thinkingLevel"] == "high"
    assert loop["request"]["extra_body"]["thinking_config"]["thinkingLevel"] == "high"
    assert two["request"]["extra_body"]["thinking_config"]["thinkingLevel"] == "low"
    assert len(scorer.calls) == 2


def test_native_gemini_argon_fails_open_on_out_of_list_or_single_answer(monkeypatch):
    for answer in ("xhigh", "minimal", "none", "ultra", ""):
        scorer = ChoiceScorer(answer)
        configure(monkeypatch, scorer, mode="always")
        body = gemini_body()
        assert gemini_call(body) is None
        assert body["extra_body"]["thinking_config"] == {"thinkingLevel": "low"}
        middleware.reset_state()


def test_native_gemini_argon_reclamps_on_route_change_without_second_call(monkeypatch):
    scorer = ChoiceScorer("high")
    configure(monkeypatch, scorer, mode="always")
    first = gemini_call(gemini_body())
    second = openrouter_call({
        "model": OPENROUTER_ARGON["model"],
        "extra_body": {"reasoning": {"effort": "low"}},
        "messages": [{"role": "user", "content": "task"}],
    }, route=OPENROUTER_ARGON)
    assert first["request"]["extra_body"]["thinking_config"]["thinkingLevel"] == "high"
    assert second["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(scorer.calls) == 1


def test_native_gemini_argon_manual_change_stands_down(monkeypatch):
    scorer = ChoiceScorer("high")
    configure(monkeypatch, scorer, mode="auto")
    first = gemini_call(gemini_body({"thinkingLevel": "medium"}))
    assert first["request"]["extra_body"]["thinking_config"]["thinkingLevel"] == "high"
    changed = gemini_body({"thinkingLevel": "low"})
    assert gemini_call(changed, turn_id="turn-2") is None
    assert changed["extra_body"]["thinking_config"]["thinkingLevel"] == "low"
    assert len(scorer.calls) == 1
    assert middleware.session_state()[f"{SESSION}/turn-2"]["manual_effort"] is True


def test_native_gemini_never_injects_even_through_effort_models(monkeypatch):
    values = {"mode": "always", "max_turns": 64, "effort_models": "gemini-4-argon,gemini-4-pro"}
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: values.get(key, default))
    scorer = ChoiceScorer("high")
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kwargs: scorer)
    for index, route in enumerate((
            GEMINI_ARGON,
            dict(GEMINI_ARGON, model="gemini-4-pro"),
            dict(GEMINI_ARGON, provider="google"))):
        body = {"model": route["model"], "messages": [{"role": "user", "content": "task"}]}
        assert gemini_call(body, turn_id=f"turn-{index}", route=route) is None
        assert "reasoning_effort" not in body and "extra_body" not in body
    # The same IDs still inject on an unrelated Chat Completions host (operator assertion).
    other = dict(GEMINI_ARGON, base_url="https://llm.example/v1")
    body = {"model": "gemini-4-argon", "messages": [{"role": "user", "content": "task"}]}
    result = gemini_call(body, turn_id="turn-9", route=other)
    assert result is not None and result["request"]["reasoning_effort"]
