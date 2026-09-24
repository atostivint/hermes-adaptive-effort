"""`llm_request` middleware: opt-in, fail-open, one Jev call per session."""

from __future__ import annotations

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")


class FakeClassifier:
    def __init__(self, score=1.0, error=None):
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


def test_default_off_never_classifies(monkeypatch, no_network):
    factory = RecordingClassifierFactory()
    use_classifier(monkeypatch, factory)
    req = supported_request()
    assert call(ctx(request=req)) is None
    assert factory.instances == []
    assert req["extra_body"]["reasoning"]["effort"] == "medium"


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
    assert out["source"] == "jev-auto"
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
    assert out["source"] == "jev-auto"


def test_auto_never_adds_an_effort_field(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    factory = RecordingClassifierFactory(score=1.9)
    use_classifier(monkeypatch, factory)
    req = request_with(messages=[{"role": "user", "content": "hi"}])
    assert call(ctx(request=req)) is None
    # Nothing to rewrite -> no Jev call either, and no field is invented.
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
    use_settings(monkeypatch, {"mode": "auto", "max_sessions": 2})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.0))
    for i in range(4):
        call(ctx(request=supported_request(), session=f"s{i}"))
    assert len(middleware.session_state()) == 2


def test_session_end_clears_state(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.0))
    call(ctx(request=supported_request(), session="s1"))
    assert "s1" in middleware.session_state()
    middleware.on_session_end(session_id="s1")
    assert "s1" not in middleware.session_state()


def test_no_network_during_auto_path(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    use_classifier(monkeypatch, RecordingClassifierFactory(score=1.9))
    out = call(ctx(request=supported_request()))
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
