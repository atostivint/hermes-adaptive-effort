"""Scorer latency histogram: bucket counts only, p95 bound in /hae status, fail-open."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")
command = import_plugin("command")

PROVIDER, MODEL, API = "openai-codex", "gpt-5.6-terra", "codex_responses"
OK = (1.9, None)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Scorer:
    """Fake scorer; every call advances the injected clock by ``duration_ms``."""

    def __init__(self, clock, duration_ms=120, outcome=OK):
        self.clock = clock
        self.duration_ms = duration_ms
        self.outcome = outcome
        self.calls = 0

    def __call__(self, timeout=None):
        return self

    def classify_detail(self, prompt):
        self.calls += 1
        self.clock.now += self.duration_ms / 1000.0
        return self.outcome


@pytest.fixture
def clock(monkeypatch):
    clk = Clock()
    monkeypatch.setattr(middleware, "_monotonic", clk)
    return clk


def configure(monkeypatch, scorer, **overrides):
    values = {"mode": "always", "subagent_mode": "always"}
    values.update(overrides)
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(middleware, "_classifier_factory", scorer)


def call(turn, session="s1"):
    return middleware.on_llm_request(
        request={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Rename a function in twelve files."}],
            "reasoning": {"effort": "medium", "summary": "auto"},
        },
        session_id=session, turn_id=turn, provider=PROVIDER, model=MODEL, api_mode=API)


def record(failure, ms, provider="jev"):
    middleware._scorer_latency_record({"scorer_provider": provider}, failure, ms)


def state(provider="jev"):
    return middleware.scorer_latency_state({"scorer_provider": provider})


@pytest.mark.parametrize("ms,bound", [
    (0, 100), (100, 100), (101, 200), (350, 350), (351, 500), (2000, 2000),
    (10000, 10000),
])
def test_value_equal_to_a_bound_lands_in_that_bucket(ms, bound):
    record(None, ms)
    assert state() == {"provider": "jev", "count": 1, "p95_le_ms": bound, "p95_over_max": False}


def test_value_above_the_last_bound_lands_in_overflow():
    record(None, 10001)
    assert state() == {"provider": "jev", "count": 1, "p95_le_ms": None, "p95_over_max": True}


def test_p95_on_a_known_distribution():
    for _ in range(94):
        record(None, 90)
    for _ in range(6):
        record(None, 700)
    # 94 of 100 are <= 100 ms (94 %), the 95th sample is the first at <= 750 ms.
    assert state()["count"] == 100
    assert state()["p95_le_ms"] == 750
    # 114 of 120 is exactly 95 %: twenty more fast samples cross the threshold.
    for _ in range(20):
        record(None, 90)
    assert state()["p95_le_ms"] == 100


def test_p95_in_overflow_reports_over_max():
    for _ in range(90):
        record(None, 50)
    for _ in range(10):
        record("timeout", 12000)
    assert state() == {"provider": "jev", "count": 100, "p95_le_ms": None, "p95_over_max": True}


def test_no_samples_state():
    assert state() == {"provider": "jev", "count": 0, "p95_le_ms": None, "p95_over_max": False}


def test_success_and_timeout_are_recorded_through_the_request_path(monkeypatch, clock):
    configure(monkeypatch, Scorer(clock, 300))
    assert call("t1") is not None
    configure(monkeypatch, Scorer(clock, 3000, (None, "timeout")))
    assert call("t2") is None
    latency = middleware.scorer_latency_state()
    assert latency["count"] == 2
    assert latency["p95_le_ms"] == 3000


@pytest.mark.parametrize("failure", [
    "credential_missing", "model_missing", "account_invalid", "endpoint_missing",
    "unsupported_provider", "classifier_error",
])
def test_configuration_failures_are_not_recorded(monkeypatch, clock, failure):
    configure(monkeypatch, Scorer(clock, 1, (None, failure)))
    call("t1")
    assert middleware.scorer_latency_state()["count"] == 0


@pytest.mark.parametrize("failure", [
    "timeout", "transport_error", "http_error", "malformed_response", "unexpected_error",
])
def test_launched_failures_are_recorded(monkeypatch, clock, failure):
    configure(monkeypatch, Scorer(clock, 50, (None, failure)))
    call("t1")
    assert middleware.scorer_latency_state()["count"] == 1


def test_paused_skip_is_not_recorded(monkeypatch, clock):
    scorer = Scorer(clock, 50, (None, "timeout"))
    configure(monkeypatch, scorer)
    for i in range(3):
        call(f"fail-{i}")
    assert middleware.scorer_latency_state()["count"] == 3
    assert call("paused") is None
    assert scorer.calls == 3
    assert middleware.scorer_latency_state()["count"] == 3


def test_fixed_single_level_route_is_not_recorded(monkeypatch, clock):
    scorer = Scorer(clock, 50)
    configure(monkeypatch, scorer)
    monkeypatch.setattr(middleware._effort, "route_choice_levels", lambda *_args: ("high",))
    call("t1")
    assert scorer.calls == 0
    assert middleware.scorer_latency_state()["count"] == 0


def test_probe_is_not_recorded(monkeypatch, clock):
    scorer = Scorer(clock, 50)
    configure(monkeypatch, scorer)
    middleware.run_probe("some operator text")
    assert scorer.calls == 1
    assert middleware.scorer_latency_state()["count"] == 0


def test_providers_are_kept_apart_and_status_shows_the_active_one():
    record(None, 90, "jev")
    record(None, 4000, "openrouter")
    assert state("jev")["p95_le_ms"] == 100
    assert state("openrouter")["p95_le_ms"] == 5000
    assert state("jev")["count"] == 1  # switching away and back keeps history


def test_reset_state_clears_everything():
    record(None, 90, "jev")
    record(None, 90, "openrouter")
    middleware.reset_state()
    assert middleware._SCORER_LATENCY == {}
    assert state("jev")["count"] == 0


def test_store_and_status_hold_counts_only(monkeypatch, clock):
    configure(monkeypatch, Scorer(clock, 120))
    assert call("turn-secret", session="session-secret") is not None
    stored = json.dumps(middleware._SCORER_LATENCY)
    payload = json.dumps(command._status_payload()["scorer_latency"])
    for text in (stored, payload):
        assert "twelve files" not in text and "session-secret" not in text
        assert MODEL not in text and "turn-secret" not in text
    assert all(isinstance(n, int) for counts in middleware._SCORER_LATENCY.values()
               for n in counts)
    assert len(middleware._SCORER_LATENCY["jev"]) == len(middleware._LATENCY_BUCKETS_MS) + 1


def test_recording_never_raises():
    middleware._scorer_latency_record(object(), None, 10.0)  # no .get: bookkeeping must swallow
    middleware._scorer_latency_record({"scorer_provider": "jev"}, None, "not-a-number")  # type: ignore[arg-type]
    assert middleware.scorer_latency_state()["count"] == 0


def test_status_text_json_and_full_rendering(monkeypatch, clock):
    configure(monkeypatch, Scorer(clock, 120))
    assert "Scorer latency" not in command._status_text()
    assert "Scorer latency: no calls yet" in command._status_full_text()
    empty = json.loads(command._status_json())["scorer_latency"]
    assert empty == {"provider": "jev", "count": 0, "p95_le_ms": None, "p95_over_max": False}

    for _ in range(42):
        record(None, 340)
    assert "Scorer latency: p95 ≤ 350 ms (42 calls)" in command._status_text()
    assert "Scorer latency: p95 ≤ 350 ms (42 calls)" in command._status_full_text()
    data = json.loads(command._status_json())
    assert data["scorer_latency"] == {
        "provider": "jev", "count": 42, "p95_le_ms": 350, "p95_over_max": False}
    assert data["schema"] == "hermes-adaptive-effort.status.v2"

    middleware.reset_state()
    for _ in range(7):
        record("timeout", 20000)
    assert "Scorer latency: p95 > 10000 ms (7 calls)" in command._status_text()
    assert json.loads(command._status_json())["scorer_latency"]["p95_over_max"] is True
