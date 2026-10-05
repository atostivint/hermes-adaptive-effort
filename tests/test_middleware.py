"""`llm_request` middleware: opt-in, fail-open, one scorer call per turn."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")

MUSE = "muse-spark-1.3-contributor-free"


class FakeClassifier:
    def __init__(self, score: "float | None" = 1.0, error=None):
        self.score = score
        self.error = error
        self.calls = []

    def classify(self, prompt):
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return self.score


class RecordingClassifierFactory:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.instances = []

    def __call__(self, timeout=None):
        client = FakeClassifier(**self.kwargs)
        client.timeout = timeout
        self.instances.append(client)
        return client


@pytest.fixture(autouse=True)
def clean_middleware(monkeypatch):
    middleware.reset_state()
    monkeypatch.setattr(middleware, "_settings_provider", None)
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    yield
    middleware.reset_state()


def use_settings(monkeypatch, settings):
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: settings.get(key, default))


def use_classifier(monkeypatch, factory):
    monkeypatch.setattr(middleware, "_classifier_factory", factory)


def request_with(**extra):
    base = {
        "model": "openrouter/x/y",
        "messages": [{"role": "system", "content": "sys"},
                     {"role": "user", "content": "first user prompt"}],
    }
    base.update(extra)
    return base


def supported_request(**extra):
    return request_with(
        extra_body={"reasoning": {"enabled": True, "effort": "medium"}}, **extra)


def ctx(session="s1", provider="openrouter", model="openrouter/x/y", **extra):
    kw = {"request": None, "session_id": session, "provider": provider, "model": model,
          "task_id": "t", "turn_id": "turn", "api_request_id": "r1", "api_mode": "chat",
          "telemetry_schema_version": "hermes.observer.v1",
          "middleware_schema_version": "hermes.middleware.v1"}
    kw.update(extra)
    return kw


def call(kw):
    return middleware.on_llm_request(**kw)


def configure_injection(monkeypatch, score=0.1, error=None, mode="inject", force_models=""):
    monkeypatch.setattr(middleware, "_config_reader", lambda: {
        "plugins": {"entries": {middleware.PLUGIN_ID: {"settings": {
            "mode": mode, "force_injection_models": force_models,
        }}}},
    })
    factory = RecordingClassifierFactory(score=score, error=error)
    use_classifier(monkeypatch, factory)
    return factory


def muse_request(**extra):
    return {"model": MUSE, "instructions": "Be concise", "store": False,
            "input": [{"role": "user", "content": "Reply with OK"}], **extra}


def muse_call(request, turn="t1", **extra):
    route = dict(request=request, provider="opencode", model=MUSE,
                 api_mode="codex_responses", turn_id=turn)
    route.update(extra)
    return call(ctx(**route))


@pytest.mark.parametrize("score,target", [(0.0, "low"), (1.0, "medium"), (2.0, "high")])
def test_inject_first_responses_request_copy_on_write(monkeypatch, score, target):
    configure_injection(monkeypatch, score)
    req = muse_request(reasoning={"summary": "auto"}, extra_body={"other": True})
    out = muse_call(req)
    assert out["request"]["reasoning"] == {"summary": "auto", "effort": target}
    assert req["reasoning"] == {"summary": "auto"}
    assert out["request"] is not req
    assert out["request"]["reasoning"] is not req["reasoning"]
    assert out["request"]["extra_body"] is req["extra_body"]
    assert out["request"]["input"] is req["input"]
    assert "reasoning_effort" not in out["request"]
    entry = middleware.session_state()["s1/t1"]
    assert (entry["state"], entry["label"], entry["target"], entry["provider"], entry["model"]) == (
        "decided", target, target, "opencode", MUSE)
    assert entry["probes"] == 1
    assert middleware.effort_change_state()["latest"]["from"] == "absent"


def test_inject_keeps_pinned_effort_across_three_turns_when_cache_unsafe(monkeypatch):
    factory = configure_injection(monkeypatch)
    # A transport with a verified shape can be marked unsafe by the shared table.
    monkeypatch.setattr(middleware._cache_safety, "effort_is_cache_safe", lambda *args: False)
    for turn in ("t1", "t2", "t3"):
        req = muse_request()
        assert muse_call(req, turn)["request"]["reasoning"]["effort"] == "low"
        assert "reasoning" not in req
        assert middleware.session_state()["s1"]["probes"] == 1
    assert len(factory.instances) == 1
    assert middleware.session_state()["s1"]["requests"] == 3
    assert len(middleware.effort_change_state()["events"]) == 1


def test_inject_safe_route_reclassifies_new_turn_but_not_tool_loop(monkeypatch):
    factory = configure_injection(monkeypatch)
    for _ in range(3):
        assert muse_call(muse_request())["request"]["reasoning"]["effort"] == "low"
    assert len(factory.instances) == 1
    factory.kwargs["score"] = 2.0
    assert muse_call(muse_request(), "t2")["request"]["reasoning"]["effort"] == "high"
    assert len(factory.instances) == 2
    applied = muse_call(muse_request(), "t2")["request"]
    assert muse_call(applied, "t2") is None
    assert len(factory.instances) == 2


@pytest.mark.parametrize("mode", ["off", "recommend", "cache_safe"])
def test_modes_other_than_auto_or_inject_do_not_inject_muse(monkeypatch, mode):
    factory = configure_injection(monkeypatch, mode=mode)
    assert muse_call(muse_request()) is None
    assert factory.instances == []


@pytest.mark.parametrize(("provider", "model"), [
    ("opencode-zen", "muse-spark-1.3"),
    ("opencode_zen", "muse-spark-1.2"),
    ("zen", MUSE),
    ("opencode-go", "muse-spark-1.3-contributor"),
    ("opencode_go", "muse-spark-1.2-contributor"),
])
def test_auto_injects_exactly_supported_muse_responses_routes(monkeypatch, provider, model):
    configure_injection(monkeypatch, mode="auto")
    request = muse_request()
    out = muse_call(request, provider=provider, model=model)
    assert out["request"]["reasoning"]["effort"] == "low"
    assert "reasoning" not in request


GO_MODEL_API_MODES = {
    "minimax-m3": "anthropic_messages", "minimax-m2.7": "anthropic_messages",
    "minimax-m2.5": "anthropic_messages", "kimi-k3": "chat_completions",
    "kimi-k2.7-code": "chat_completions", "kimi-k2.6": "chat_completions",
    "longcat-2.0": "chat_completions", "kimi-k2.5": "chat_completions",
    "glm-5.2": "chat_completions", "glm-5.3-flash": "chat_completions",
    "glm-5.3": "chat_completions", "glm-5.1": "chat_completions",
    "glm-5": "chat_completions", "deepseek-v4-pro": "chat_completions",
    "deepseek-v4-flash": "chat_completions", "deepseek-flash": "chat_completions",
    "deepseek-v4.1-flash": "chat_completions",
    "deepseek-v4-flash-vision-exp": "chat_completions",
    "qwen3.7-max": "anthropic_messages", "qwen3.8-max": "anthropic_messages",
    "qwen3.8-flash": "anthropic_messages", "qwen3.7-plus": "anthropic_messages",
    "qwen3.6-plus": "anthropic_messages", "qwen3.5-plus": "anthropic_messages",
    "mimo-v2-pro": "chat_completions", "mimo-v2-omni": "chat_completions",
    "mimo-v2.6-pro": "chat_completions", "mimo-v2.6-flash": "chat_completions",
    "space-bunny-free": "chat_completions", "longcat-2.5-preview-free": "chat_completions",
    "mimo-v2.5-pro": "chat_completions", "mimo-v2.5": "chat_completions",
    "hy4-preview": "chat_completions", "hy3": "chat_completions",
    "hy3-preview": "chat_completions", "gpt-5.6-luna": "codex_responses",
    "grok-4.5": "codex_responses", "grok-4.7": "codex_responses",
    "grok-4.6": "codex_responses",
    "muse-spark-1.3-contributor": "codex_responses",
    "muse-spark-1.2-contributor": "codex_responses",
    "omen-alpha": "chat_completions", "gpt-6-luna": "codex_responses",
}

GO_INJECTION_MODELS = {
    "gpt-5.6-luna", "gpt-6-luna", "grok-4.5", "grok-4.6", "grok-4.7",
    "muse-spark-1.3-contributor", "muse-spark-1.2-contributor", "glm-5.2",
    "glm-5.3", "kimi-k3", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4.1-flash",
}


@pytest.mark.parametrize("model,api_mode", sorted(GO_MODEL_API_MODES.items()))
def test_every_published_go_model_has_an_explicit_injection_outcome(
        monkeypatch, model, api_mode):
    factory = configure_injection(monkeypatch, mode="auto")
    extra = {"thinking": {"type": "enabled"}} if model in {
        "glm-5.3", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4.1-flash",
    } else {}
    request = ({"model": model, "input": [{"role": "user", "content": "Reply OK"}]}
               if api_mode == "codex_responses" else
               {"model": model, "messages": [{"role": "user", "content": "Reply OK"}]})
    if extra:
        request["extra_body"] = extra
    result = call(ctx(request=request, provider="opencode-go", model=model,
                      api_mode=api_mode))
    if model in GO_INJECTION_MODELS:
        assert result is not None, model
        changed = result["request"]
        if api_mode == "codex_responses":
            assert changed["reasoning"]["effort"] == "low", model
        else:
            assert changed["reasoning_effort"] in {"low", "high"}, model
        assert len(factory.instances) == 1, model
    else:
        assert result is None, model
        assert len(factory.instances) == 0, model


@pytest.mark.parametrize("model", ["glm-5.3", "deepseek-v4-pro", "deepseek-v4-flash",
                                    "deepseek-v4.1-flash"])
def test_go_paired_controls_must_already_be_enabled_and_are_preserved(monkeypatch, model):
    factory = configure_injection(monkeypatch, mode="auto")
    for index, extra in enumerate(({}, {"thinking": {"type": "disabled"}},
                                   {"thinking": "bad"})):
        request = {"model": model, "messages": [{"role": "user", "content": "Reply OK"}],
                   "extra_body": extra}
        assert call(ctx(request=request, provider="opencode-go", model=model,
                        api_mode="chat_completions", session=f"bad-{index}")) is None
        assert "reasoning_effort" not in request
    assert factory.instances == []

    for index, disabled_or_bad in enumerate(({"enabled": False}, "bad")):
        request = {"model": model, "messages": [{"role": "user", "content": "Reply OK"}],
                   "reasoning": disabled_or_bad,
                   "extra_body": {"thinking": {"type": "enabled"}}}
        assert call(ctx(request=request, provider="opencode-go", model=model,
                        api_mode="chat_completions", session=f"disabled-{index}")) is None
    assert factory.instances == []

    extra = {"thinking": {"type": "enabled"}, "provider_option": "keep"}
    request = {"model": model, "messages": [{"role": "user", "content": "Reply OK"}],
               "extra_body": extra}
    out = call(ctx(request=request, provider="opencode-go", model=model,
                   api_mode="chat_completions"))
    assert out["request"]["reasoning_effort"] == "low"
    assert out["request"]["extra_body"] is extra
    assert extra == {"thinking": {"type": "enabled"}, "provider_option": "keep"}
    assert "reasoning_effort" not in request
    assert len(factory.instances) == 1


def test_auto_go_responses_injection_pins_unsafe_route_across_applied_requests(monkeypatch):
    factory = configure_injection(monkeypatch, mode="auto")
    monkeypatch.setattr(middleware._cache_safety, "effort_is_cache_safe", lambda *args: False)

    def go_call(request, turn):
        return call(ctx(request=request, provider="opencode-go", model="grok-4.7",
                        api_mode="codex_responses", turn_id=turn))

    first = go_call({"model": "grok-4.7", "input": [{"role": "user", "content": "Do it"}]}, "t1")
    second = go_call({"model": "grok-4.7", "input": [{"role": "user", "content": "Again"}]}, "t2")
    assert second["request"]["reasoning"]["effort"] == "low"
    assert go_call(first["request"], "t3") is None
    assert len(factory.instances) == 1


def test_forced_injection_models_support_exact_operator_list_and_known_containers(monkeypatch):
    factory = configure_injection(monkeypatch, mode="auto",
                                  force_models="custom/model-one,\n model-two ")
    response_request = {"model": "model-one", "input": [{"role": "user", "content": "Do it"}],
                        "reasoning": {"summary": "auto"}}
    out = call(ctx(request=response_request, provider="custom-provider", model="model-one",
                   api_mode="codex_responses"))
    assert out["request"]["reasoning"] == {"summary": "auto", "effort": "low"}
    assert response_request["reasoning"] == {"summary": "auto"}

    chat_request = {"model": "model-two", "messages": [{"role": "user", "content": "Do it"}]}
    out = call(ctx(request=chat_request, provider="custom-provider", model="model-two",
                   api_mode="chat_completions"))
    assert out["request"]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in chat_request
    assert len(factory.instances) == 1
    near_miss = {"model": "model-one-extra",
                 "messages": [{"role": "user", "content": "Do it"}]}
    assert call(ctx(request=near_miss, provider="custom-provider", model="model-one-extra",
                    api_mode="chat_completions")) is None
    assert "reasoning_effort" not in near_miss
    assert len(factory.instances) == 1


def test_force_injection_models_default_empty_and_normalize_exact_tokens():
    assert middleware.DEFAULTS["force_injection_models"] == ""
    assert middleware._normalize_forced_models(" provider/Model-A,\nmodel-b ") == (
        "model-a", "model-b")
    assert middleware._normalize_forced_models(["model-a"]) == ()


@pytest.mark.parametrize("mode", ["off", "recommend", "cache_safe"])
def test_forced_injection_does_not_change_modes_outside_auto_or_inject(monkeypatch, mode):
    factory = configure_injection(monkeypatch, mode=mode, force_models="manual-model")
    request = {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}]}
    assert call(ctx(request=request, provider="custom", model="manual-model",
                    api_mode="chat_completions")) is None
    assert "reasoning_effort" not in request
    assert factory.instances == []


@pytest.mark.parametrize("overrides", [
    {"api_mode": "unknown"},
    {"request": {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}],
                  "reasoning": {"enabled": False}}},
    {"request": {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}],
                  "extra_body": {"thinking": {"type": "disabled"}}}},
    {"request": {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}],
                  "reasoning_effort": None}},
])
def test_forced_injection_still_requires_known_shape_and_enabled_controls(monkeypatch, overrides):
    factory = configure_injection(monkeypatch, mode="auto", force_models="manual-model")
    request = {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}]}
    request.update(overrides.pop("request", {}))
    assert call(ctx(request=request, provider="custom", model="manual-model",
                    api_mode=overrides.pop("api_mode", "chat_completions"))) is None
    assert factory.instances == []


@pytest.mark.parametrize("enabled", ["false", 0])
def test_forced_injection_rejects_malformed_reasoning_enabled_flag(monkeypatch, enabled):
    factory = configure_injection(monkeypatch, mode="auto", force_models="manual-model")
    request = {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}],
               "reasoning": {"enabled": enabled}}
    result = call(ctx(request=request, provider="custom", model="manual-model",
                      api_mode="chat_completions"))
    assert result is None
    assert request["reasoning"] == {"enabled": enabled}
    assert "reasoning_effort" not in request
    assert factory.instances == []


@pytest.mark.parametrize("mode", ["auto", "inject"])
def test_existing_effort_is_not_rewritten_when_thinking_is_disabled(monkeypatch, mode):
    factory = configure_injection(monkeypatch, mode=mode, force_models="manual-model")
    request = {"model": "manual-model", "messages": [{"role": "user", "content": "Do it"}],
               "reasoning_effort": "high", "extra_body": {"thinking": {"type": "disabled"}}}
    assert call(ctx(request=request, provider="custom", model="manual-model",
                    api_mode="chat_completions")) is None
    assert request["reasoning_effort"] == "high"
    assert request["extra_body"]["thinking"]["type"] == "disabled"
    assert factory.instances == []


@pytest.mark.parametrize("overrides", [
    {"model": "muse-spark-2.0"},
    {"model": "muse-spark-1.3-contributor", "provider": "opencode-zen"},
    {"provider": "unknown"},
    {"api_mode": "chat_completions"},
])
def test_auto_does_not_inject_without_positive_route_support(monkeypatch, overrides):
    factory = configure_injection(monkeypatch, mode="auto")
    request = muse_request()
    assert muse_call(request, **overrides) is None
    assert "reasoning" not in request
    assert factory.instances == []


def test_auto_injection_respects_disabled_controls_without_scoring(monkeypatch):
    factory = configure_injection(monkeypatch, mode="auto")
    assert muse_call(muse_request(reasoning={"enabled": False})) is None
    assert muse_call(muse_request(extra_body={"reasoning": {"enabled": False}})) is None
    assert factory.instances == []


@pytest.mark.parametrize("mode", ["auto", "inject"])
def test_existing_top_level_effort_stays_disabled_in_both_modes(monkeypatch, mode):
    factory = configure_injection(monkeypatch, mode=mode)
    request = muse_request(reasoning={"effort": "high", "enabled": False})
    assert muse_call(request) is None
    assert request["reasoning"] == {"effort": "high", "enabled": False}
    assert factory.instances == []


def test_auto_unsafe_injection_stays_pinned_for_bare_and_applied_requests(monkeypatch):
    factory = configure_injection(monkeypatch, mode="auto")
    monkeypatch.setattr(middleware._cache_safety, "effort_is_cache_safe", lambda *args: False)
    first = muse_call(muse_request(), "t1")
    second = muse_call(muse_request(), "t2")
    assert second["request"]["reasoning"]["effort"] == "low"
    assert muse_call(first["request"], "t3") is None
    assert len(factory.instances) == 1
    assert middleware.session_state()["s1"]["probes"] == 1
    assert middleware.session_state()["s1"]["requests"] == 3


@pytest.mark.parametrize("overrides", [
    {"model": "gpt-4o"}, {"model": "muse-spark-unknown"},
    {"provider": "unknown"}, {"api_mode": "unknown"}, {"api_mode": "anthropic_messages"},
])
def test_inject_ineligible_route_is_silent_noop(monkeypatch, overrides):
    factory = configure_injection(monkeypatch)
    req = muse_request()
    assert muse_call(req, **overrides) is None
    assert "reasoning" not in req
    assert factory.instances == []


@pytest.mark.parametrize("fields", [
    {"reasoning": {"effort": "none"}}, {"reasoning_effort": "none"},
    {"reasoning": {"enabled": False}}, {"reasoning": {"effort": "high", "enabled": False}},
    {"extra_body": {"reasoning": {"enabled": False}}},
    {"reasoning": "bad"}, {"reasoning": {"effort": 1}}, {"reasoning_effort": None},
    {"extra_body": "bad"}, {"thinking": {"type": "disabled"}},
])
def test_inject_never_overwrites_disabled_or_malformed_controls(monkeypatch, fields):
    factory = configure_injection(monkeypatch)
    assert muse_call(muse_request(**fields)) is None
    assert factory.instances == []


@pytest.mark.parametrize("score,error", [(None, RuntimeError("classifier failed")),
                                          (float("nan"), None), (True, None), (3.0, None)])
def test_inject_classifier_failure_is_fail_open_and_memoized(monkeypatch, score, error):
    factory = configure_injection(monkeypatch, score, error)
    req = muse_request()
    assert muse_call(req) is None
    assert muse_call(req) is None
    assert "reasoning" not in req
    assert len(factory.instances) == 1
    assert middleware.session_state()["s1/t1"]["state"] == "failed"


@pytest.mark.parametrize("payload", [None, [], "bad", {"input": [None, {"role": "user", "content": 1}]}])
def test_inject_malformed_payload_fails_open(monkeypatch, payload):
    factory = configure_injection(monkeypatch)
    assert muse_call(payload) is None
    assert factory.instances == []


def test_enabling_inject_authorizes_selected_scorer(monkeypatch):
    factory = configure_injection(monkeypatch)
    assert muse_call(muse_request())["request"]["reasoning"]["effort"] == "low"
    assert len(factory.instances) == 1


def test_inject_rejects_unlisted_chat_api_mode(monkeypatch):
    factory = configure_injection(monkeypatch)
    req = request_with(model=MUSE)
    out = muse_call(req, api_mode="chat_completions")
    assert out is None
    assert "reasoning" not in req
    assert factory.instances == []


def test_inject_rewrites_existing_effort_and_pins_unknown_api_mode(monkeypatch):
    factory = configure_injection(monkeypatch)
    req = request_with(model=MUSE, reasoning={"effort": "medium"})
    first = muse_call(req, "t1", api_mode="future_api_mode")
    assert first["request"]["reasoning"]["effort"] == "low"
    assert muse_call(request_with(model=MUSE), "t2", api_mode="future_api_mode") is None
    assert len(factory.instances) == 1
    assert middleware.session_state()["s1"]["probes"] == 1


def test_child_effective_mode_controls_cache_key_and_injection(monkeypatch):
    factory = configure_injection(monkeypatch)
    monkeypatch.setattr(middleware, "_config_reader", lambda: {
        "plugins": {"entries": {middleware.PLUGIN_ID: {"settings": {
            "mode": "inject", "subagent_mode": "auto",
        }}}},
    })
    middleware.on_subagent_start(parent_session_id="parent", child_session_id="s1",
                                 child_goal="Refactor this module")
    first = muse_request(reasoning={"effort": "medium"})
    assert muse_call(first, "t1", api_mode="unknown")["request"]["reasoning"]["effort"] == "low"
    factory.kwargs["score"] = 2.0
    second = muse_request(reasoning={"effort": "medium"})
    assert muse_call(second, "t2", api_mode="unknown")["request"]["reasoning"]["effort"] == "high"
    assert len(factory.instances) == 2
    assert {key for key in middleware.session_state() if key.startswith("s1/")} == {
        "s1/t1", "s1/t2"}


def test_child_inject_mode_uses_unsafe_route_session_pin(monkeypatch):
    factory = configure_injection(monkeypatch, mode="auto")
    monkeypatch.setattr(middleware, "_config_reader", lambda: {
        "plugins": {"entries": {middleware.PLUGIN_ID: {"settings": {
            "mode": "auto", "subagent_mode": "inject",
        }}}},
    })
    monkeypatch.setattr(middleware._cache_safety, "effort_is_cache_safe", lambda *args: False)
    middleware.on_subagent_start(parent_session_id="parent", child_session_id="s1",
                                 child_goal="Refactor this module")
    for turn in ("t1", "t2"):
        request = muse_request()
        assert muse_call(request, turn, api_mode="codex_responses")["request"]["reasoning"]["effort"] == "low"
    assert len(factory.instances) == 1
    assert middleware.session_state()["s1"]["probes"] == 1


def test_inject_survives_responses_builder_and_preflight(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from agent.codex_responses_adapter import _preflight_codex_api_kwargs
    from agent.transports import codex

    monkeypatch.setattr(codex, "_profile_declared_efforts", lambda *args: ())

    configure_injection(monkeypatch)
    request = codex.ResponsesApiTransport().build_kwargs(
        model=MUSE,
        messages=[{"role": "user", "content": "Reply with OK"}],
        instructions="Be concise",
        provider="opencode",
    )
    assert "reasoning" not in request
    out = muse_call(request)
    assert out["request"]["reasoning"]["effort"] == "low"
    prepared = _preflight_codex_api_kwargs(out["request"])
    assert prepared["reasoning"] == {"effort": "low"}


def test_default_auto_classifies_and_rewrites_supported_effort(monkeypatch, no_network):
    factory = RecordingClassifierFactory(score=2.0)
    use_classifier(monkeypatch, factory)
    req = supported_request()
    result = call(ctx(request=req))
    assert result["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(factory.instances) == 1
    assert factory.instances[0].calls == ["first user prompt"]
    entry = next(iter(middleware.session_state().values()))
    assert (entry["state"], entry["mode"], entry["target"], entry["probes"]) == (
        "decided", "auto", "high", 1)


def test_explicit_off_never_classifies_and_keeps_route_visible(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "off"})
    factory = RecordingClassifierFactory()
    use_classifier(monkeypatch, factory)
    req = supported_request()
    assert call(ctx(request=req)) is None
    assert factory.instances == []
    assert req["extra_body"]["reasoning"]["effort"] == "medium"
    entry = next(iter(middleware.session_state().values()))
    assert (entry["state"], entry["provider"], entry["model"], entry["api_mode"]) == (
        "off", "openrouter", "openrouter/x/y", "chat")
    assert entry["probes"] == 0
    assert "first user prompt" not in json.dumps(middleware.session_state())


def test_invalid_mode_is_off(monkeypatch):
    use_settings(monkeypatch, {"mode": "banana"})
    factory = RecordingClassifierFactory()
    use_classifier(monkeypatch, factory)
    assert call(ctx(request=supported_request())) is None
    assert factory.instances == []


def test_recommend_records_but_does_not_mutate(monkeypatch):
    use_settings(monkeypatch, {"mode": "recommend"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    req = supported_request()
    out = call(ctx(request=req))
    assert out is not None
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "medium"
    assert out["source"] == "hermes-adaptive-effort"
    assert "high" in out["reason"] and "not applied" in out["reason"]
    assert factory.instances[0].calls == ["first user prompt"]


def test_auto_rewrites_only_the_effort_value(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    req = supported_request()
    out = call(ctx(request=req))
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    # Everything else is byte-for-byte identical.
    before, after = dict(req), dict(out["request"])
    assert after["messages"] == before["messages"]
    assert after["model"] == before["model"]
    assert out["source"] == "hermes-adaptive-effort"


def test_auto_never_adds_an_effort_field(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    req = request_with(messages=[{"role": "user", "content": "hi"}])
    assert call(ctx(request=req)) is None
    # Nothing to rewrite -> no scorer call either, and no field is invented.
    assert factory.instances == []
    assert "reasoning_effort" not in req and "extra_body" not in req


def test_top_level_reasoning_effort_is_supported(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=0.1))
    req = request_with(reasoning_effort="medium")
    out = call(ctx(request=req))
    assert out["request"]["reasoning_effort"] == "low"


def test_recommended_effort_is_clamped_to_route_vocabulary(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    req = supported_request()
    out = call(ctx(request=req, model="openrouter/x/y"))
    # Route vocabulary accepts "high" verbatim.
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"


def test_second_request_reuses_decision_without_second_jev_call(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    first = call(ctx(request=supported_request()))
    assert first["request"]["extra_body"]["reasoning"]["effort"] == "high"
    # A tool-loop follow-up: different messages, same session.
    follow = supported_request()
    follow["messages"].append({"role": "assistant", "content": "ok"})
    follow["messages"].append({"role": "user", "content": "tool result"})
    out = call(ctx(request=follow, api_call_count=1))
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(factory.instances) == 1
    assert len(factory.instances[0].calls) == 1


def test_separate_sessions_do_not_share_a_decision(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    call(ctx(request=supported_request(), session="s1"))
    factory.instances[0].score = 0.1
    call(ctx(request=supported_request(), session="s2"))
    assert len(factory.instances) == 2


def test_classifier_none_fails_open(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=None))
    req = supported_request()
    assert call(ctx(request=req)) is None
    assert req["extra_body"]["reasoning"]["effort"] == "medium"


def test_classifier_exception_fails_open(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(error=TimeoutError("slow")))
    req = supported_request()
    assert call(ctx(request=req)) is None
    assert req["extra_body"]["reasoning"]["effort"] == "medium"


def test_failed_session_is_not_retried(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(error=TimeoutError("slow"))
    use_classifier(monkeypatch, factory)
    call(ctx(request=supported_request(), session="s1"))
    call(ctx(request=supported_request(), session="s1"))
    assert len(factory.instances) == 1


def test_disabled_reasoning_is_never_re_enabled(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    req = request_with(extra_body={"reasoning": {"enabled": False, "effort": "none"}})
    assert call(ctx(request=req)) is None
    assert req["extra_body"]["reasoning"]["enabled"] is False
    # Disabled reasoning is not an open invitation: no classification at all.
    assert factory.instances == []


def test_missing_session_fails_open(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory()
    use_classifier(monkeypatch, factory)
    kw = ctx(request=supported_request())
    kw["session_id"] = ""
    assert call(kw) is None
    assert factory.instances == []


def test_no_prompt_text_is_stored(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.0))
    marker = "UNIQUE_SECRET_PROMPT_MARKER"
    req = supported_request()
    req["messages"][1]["content"] = marker
    call(ctx(request=req))
    blob = repr(dict(middleware.session_state()))
    assert marker not in blob


def test_session_state_is_bounded(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "max_turns": 2})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.0))
    for i in range(4):
        call(ctx(request=supported_request(), session=f"s{i}"))
    assert len(middleware.session_state()) == 2


def test_session_end_clears_state(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.0))
    call(ctx(request=supported_request(), session="s1"))
    # Decisions are keyed per (session, turn); ending a session clears them all.
    assert "s1/turn" in middleware.session_state()
    middleware.on_session_end(session_id="s1")
    assert "s1/turn" not in middleware.session_state()


def test_no_network_during_auto_path(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    out = call(ctx(request=supported_request()))
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"


# ── what /hermes-adaptive-effort status reports ──────────────────────────────────────────

class DetailedClassifier(FakeClassifier):
    """Reports *why* it failed, the way JevClient.classify_detail does.

    ``entered``/``release`` let a test hold a probe open to observe concurrency.
    """

    def __init__(self, score: "float | None" = 1.0, failure=None, entered=None, release=None):
        super().__init__(score=score)
        self.failure = failure
        self.entered = entered
        self.release = release

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(10)
        return self.score, self.failure


def test_session_entry_records_score_target_timing_and_counts(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    assert call(ctx(request=supported_request(), session="s1")) is not None

    entry = middleware.session_state()["s1/turn"]
    assert entry["state"] == "decided"
    assert entry["score"] == pytest.approx(1.9)
    assert entry["label"] == "high"
    assert entry["target"] == "high"
    assert entry["requests"] == 1
    assert entry["probes"] == 1
    assert entry["elapsed_ms"] >= 0
    assert entry["failure"] is None
    assert entry["mode"] == "auto"
    assert entry["updated_at"] > 0

    # A follow-up reuses the decision: count it, never probe twice.
    assert call(ctx(request=supported_request(), session="s1")) is not None
    entry = middleware.session_state()["s1/turn"]
    assert entry["requests"] == 2
    assert entry["probes"] == 1


def test_session_entry_records_the_failure_reason(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})

    class Factory:
        def __call__(self, timeout=None):
            return DetailedClassifier(score=None, failure="timeout")

    use_classifier(monkeypatch, Factory)
    assert call(ctx(request=supported_request(), session="s1")) is None
    entry = middleware.session_state()["s1/turn"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "timeout"
    assert entry["score"] is None
    assert entry["probes"] == 1


def test_missing_credential_is_reported_without_opening_a_socket(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    monkeypatch.setattr(middleware._jev_client, "_default_key_reader", lambda: "")
    assert call(ctx(request=supported_request(), session="s1")) is None
    entry = middleware.session_state()["s1/turn"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "credential_missing"


def test_openrouter_adapter_uses_the_configured_model_and_common_rewrite(monkeypatch):
    use_settings(monkeypatch, {
        "mode": "auto", "scorer_provider": "openrouter", "scorer_model": "openai/gpt-4o-mini",
    })
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    classifier = DetailedClassifier(score=2.0)
    calls = []

    def build_client(settings):
        calls.append(settings["scorer_provider"])
        return classifier, None

    monkeypatch.setattr(middleware._scorers, "build_client", build_client)
    result = call(ctx(request=supported_request(), session="openrouter"))

    assert calls == ["openrouter"]
    assert result["request"]["extra_body"]["reasoning"]["effort"] == "high"
    entry = middleware.session_state()["openrouter/turn"]
    assert entry["scorer_provider"] == "openrouter"
    assert entry["scorer_model"] == "openai/gpt-4o-mini"
    assert entry["probes"] == 1


def test_openrouter_without_a_model_is_reported_and_not_retried(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "scorer_provider": "openrouter"})
    monkeypatch.setattr(middleware, "_classifier_factory", None)

    assert call(ctx(request=supported_request(), session="missing-model")) is None
    entry = middleware.session_state()["missing-model/turn"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "model_missing"
    assert entry["probes"] == 1


def test_cloudflare_failure_is_memoized_for_turn_without_jev_fallback(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "scorer_provider": "cloudflare",
        "cloudflare_account_id": "0123456789abcdef0123456789abcdef"})
    monkeypatch.setattr(middleware._scorers.cloudflare_client, "_default_key_reader", lambda: "")
    monkeypatch.setattr(middleware._jev_client, "_default_key_reader",
                        lambda: (_ for _ in ()).throw(AssertionError("Jev fallback")))
    request = supported_request()
    assert call(ctx(request=request, session="cloudflare-fail")) is None
    assert call(ctx(request=request, session="cloudflare-fail")) is None
    entry = middleware.session_state()["cloudflare-fail/turn"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "credential_missing"
    assert entry["probes"] == 1


def test_cloudflare_adapter_success_rewrites_the_existing_field(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "scorer_provider": "cloudflare",
        "cloudflare_account_id": "0123456789abcdef0123456789abcdef"})
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    monkeypatch.setattr(middleware._scorers.cloudflare_client, "_default_key_reader",
                        lambda: "test-token")
    calls = []

    def transport(request, _timeout):
        calls.append(request.full_url)
        return {"success": True, "errors": [], "result": {"answers": {
            "effort": {"score": 0.1}}}}

    monkeypatch.setattr(middleware._scorers.cloudflare_client, "_default_transport", transport)
    result = call(ctx(request=supported_request(), session="cloudflare-success"))
    assert result["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert calls == ["https://api.cloudflare.com/client/v4/accounts/"
                     "0123456789abcdef0123456789abcdef/ai/run/@cf/cloudflare/clef"]
    entry = middleware.session_state()["cloudflare-success/turn"]
    assert entry["scorer_provider"] == "cloudflare"
    assert entry["scorer_model"] == "@cf/cloudflare/clef"
    assert entry["probes"] == 1


def test_custom_chat_completions_scorer_rewrites(monkeypatch):
    use_settings(monkeypatch, {
        "mode": "auto",
        "scorer_provider": "custom",
        "scorer_model": "local-rubric-4b",
        "custom_endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "custom_api_format": "chat_completions",
        "custom_auth": "none",
    })
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    monkeypatch.setattr(
        middleware._scorers.custom_client, "_default_key_reader",
        lambda: (_ for _ in ()).throw(AssertionError("auth=none must not read a key")))
    requests = []

    def transport(request, _timeout):
        requests.append(request)
        return {"choices": [{"message": {"content": '{"score": 1.9}'}}]}

    monkeypatch.setattr(middleware._scorers.custom_client, "_default_transport", transport)
    out = call(ctx(request=supported_request(), session="custom-scorer"))

    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(requests) == 1
    assert requests[0].full_url == "http://127.0.0.1:8080/v1/chat/completions"
    body = json.loads(requests[0].data.decode("utf-8"))
    assert body["model"] == "local-rubric-4b"
    assert body["messages"][-1]["content"] == "first user prompt"
    assert requests[0].get_header("Authorization") is None
    entry = middleware.session_state()["custom-scorer/turn"]
    assert entry["scorer_provider"] == "custom"
    assert entry["scorer_model"] == "local-rubric-4b"
    assert entry["probes"] == 1


def test_unknown_scorer_provider_fails_open_without_fallback(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "scorer_provider": "not-a-provider"})
    monkeypatch.setattr(middleware, "_classifier_factory", None)

    assert call(ctx(request=supported_request(), session="unknown-scorer")) is None
    entry = middleware.session_state()["unknown-scorer/turn"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "unsupported_provider"
    assert entry["probes"] == 1


# ── criterion: never more than one in-flight probe per session ─────────────

def test_only_one_probe_runs_per_session_at_a_time(monkeypatch):
    import threading

    use_settings(monkeypatch, {"mode": "auto"})
    entered, release = threading.Event(), threading.Event()
    clients = []

    def factory(timeout=None):
        client = DetailedClassifier(score=1.9, entered=entered, release=release)
        clients.append(client)
        return client

    use_classifier(monkeypatch, factory)

    results = {}

    def first_request():
        results["a"] = call(ctx(request=supported_request(), session="s1"))

    thread = threading.Thread(target=first_request)
    thread.start()
    assert entered.wait(10)                       # A is inside the probe
    assert middleware.in_flight() == ("s1/turn",)

    # Same session, second request while the first probe is still running.
    assert call(ctx(request=supported_request(), session="s1")) is None
    assert len(clients) == 1                      # no second scorer call

    release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert results["a"] is not None               # the in-flight one still won

    assert middleware.in_flight() == ()
    entry = middleware.session_state()["s1/turn"]
    assert entry["probes"] == 1
    assert entry["requests"] == 2


def test_in_flight_is_released_when_the_probe_fails(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(error=TimeoutError("slow")))
    assert call(ctx(request=supported_request(), session="s1")) is None
    assert middleware.in_flight() == ()
    entry = middleware.session_state()["s1/turn"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "classifier_error"


def test_reset_state_also_clears_an_in_flight_probe():
    assert middleware._claim("s1/turn") is True
    assert middleware.in_flight() == ("s1/turn",)
    middleware.reset_state()
    assert middleware.in_flight() == ()
    assert middleware._claim("s1/turn") is True        # claimable again after reset


# ── applied-change feed (consumed by the desktop backend's ``GET /changes``) ──


def _feed():
    return middleware.effort_change_state()


def test_applied_rewrite_is_recorded(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    assert call(ctx(request=supported_request())) is not None
    feed = _feed()
    assert [(e["from"], e["to"]) for e in feed["events"]] == [("medium", "high")]
    assert feed["events"][0]["id"] == 1
    assert feed["latest"] == feed["events"][0]
    assert isinstance(feed["stream_id"], str) and feed["stream_id"]


def test_re_sending_the_applied_effort_records_nothing(monkeypatch, no_network):
    """A tool loop inside one turn re-sends the value we already wrote: no new event."""
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    first = call(ctx(request=supported_request()))
    assert call(ctx(request=first["request"])) is None
    assert len(_feed()["events"]) == 1


def test_the_same_rewrite_is_recorded_once_per_decision(monkeypatch, no_network):
    """A route change re-sends the request at its original level: one event, not two."""
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    assert call(ctx(request=supported_request())) is not None
    again = call(ctx(request=supported_request()))
    assert again["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(_feed()["events"]) == 1


def test_nothing_that_failed_open_is_recorded(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(error=TimeoutError("slow")))
    assert call(ctx(request=supported_request(), session="s1")) is None
    use_settings(monkeypatch, {"mode": "recommend"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    assert call(ctx(request=supported_request(), session="s2")) is not None
    use_settings(monkeypatch, {"mode": "auto"})
    assert call(ctx(request=request_with(), session="s3")) is None  # nothing writable
    feed = _feed()
    assert feed["events"] == [] and feed["latest"] is None


def test_feed_carries_effort_values_only(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    call(ctx(request=supported_request()))
    feed = _feed()
    assert len(feed["events"]) == 1  # a rewrite really happened
    assert "first user prompt" not in json.dumps(feed)
    assert set(feed["events"][0]) == {"id", "from", "to", "at"}


def test_reset_state_starts_a_new_stream(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    call(ctx(request=supported_request()))
    before = _feed()["stream_id"]
    middleware.reset_state()
    after = _feed()
    assert after["stream_id"] != before  # a consumer must drop its cursor here
    assert after["events"] == [] and after["latest"] is None


def test_feed_is_bounded(monkeypatch, no_network):
    """A long-lived process must not grow the feed without limit, and ids must keep counting."""
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    limit = middleware._CHANGE_HISTORY_LIMIT
    for index in range(limit + 5):
        assert call(ctx(request=supported_request(), session=f"s{index}")) is not None
    feed = _feed()
    assert len(feed["events"]) == limit
    assert feed["events"][0]["id"] == 6
    assert feed["latest"]["id"] == limit + 5
