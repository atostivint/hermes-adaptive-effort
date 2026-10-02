"""`llm_request` middleware: opt-in, fail-open, one Jev call per session."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")


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
    assert out["source"] == "jev-auto-effort"
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
    assert out["source"] == "jev-auto-effort"


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


# ── what /jev-auto-effort status reports ──────────────────────────────────────────

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
    assert len(clients) == 1                      # no second Jev call

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
