"""Scorer circuit breaker: repeated failures pause the selected scorer, fail-open."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")
command = import_plugin("command")

PROVIDER, MODEL, API = "openai-codex", "gpt-5.6-terra", "codex_responses"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Scorer:
    """Fake scorer; each call pops the next ``(score, failure)`` (last one repeats)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, timeout=None):
        return self

    def classify_detail(self, prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        return outcome


FAIL = (None, "timeout")
OK = (1.9, None)


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
    return values


def body():
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Rename a function in twelve files."}],
        "reasoning": {"effort": "medium", "summary": "auto"},
    }


def call(turn, session="s1"):
    return middleware.on_llm_request(
        request=body(), session_id=session, turn_id=turn, provider=PROVIDER, model=MODEL,
        api_mode=API)


def turn_entry(turn, session="s1"):
    return middleware.session_state()[middleware._decision_key(session, turn)]


def trip(count=3):
    for i in range(count):
        assert call(f"fail-{i}") is None


def test_threshold_failures_pause_the_scorer_and_leave_the_request_unchanged(
        monkeypatch, clock):
    scorer = Scorer(FAIL)
    configure(monkeypatch, scorer)
    trip()
    assert scorer.calls == 3
    assert call("paused-turn") is None
    assert scorer.calls == 3  # no scorer call while paused
    entry = turn_entry("paused-turn")
    assert entry["state"] == "failed" and entry["failure"] == "scorer_paused"
    # A second request of the same turn does not call the scorer either.
    assert call("paused-turn") is None
    assert scorer.calls == 3


def test_below_threshold_keeps_calling(monkeypatch, clock):
    scorer = Scorer(FAIL)
    configure(monkeypatch, scorer)
    trip(2)
    call("again")
    assert scorer.calls == 3


def test_cooldown_ends_with_one_trial_and_trial_success_resets(monkeypatch, clock):
    scorer = Scorer(FAIL, FAIL, FAIL, OK, FAIL)
    configure(monkeypatch, scorer)
    trip()
    clock.now += 59
    assert call("still-paused") is None and scorer.calls == 3
    clock.now += 2
    result = call("trial")
    assert scorer.calls == 4
    assert result is not None and result["request"]["reasoning"]["effort"] == "high"
    state = middleware.scorer_pause_state()
    assert state["failures"] == 0 and state["paused"] is False
    # Counting restarts from zero: one failure does not pause.
    call("after-1")
    call("after-2")
    assert scorer.calls == 6


def test_trial_failure_reopens_the_pause_immediately(monkeypatch, clock):
    scorer = Scorer(FAIL)
    configure(monkeypatch, scorer)
    trip()
    clock.now += 61
    call("trial")
    assert scorer.calls == 4
    assert middleware.scorer_pause_state()["paused"] is True
    call("blocked")
    assert scorer.calls == 4
    assert turn_entry("blocked")["failure"] == "scorer_paused"
    clock.now += 61
    call("trial-2")
    assert scorer.calls == 5


@pytest.mark.parametrize("code", [
    "credential_missing", "model_missing", "account_missing", "account_invalid",
    "endpoint_missing", "endpoint_invalid", "unsupported_provider",
    "unsupported_api_format", "unsupported_auth", "invalid_prompt", "classifier_error"])
def test_configuration_failures_never_open_the_pause(monkeypatch, clock, code):
    scorer = Scorer((None, code))
    configure(monkeypatch, scorer)
    for i in range(6):
        call(f"t{i}")
    assert scorer.calls == 6
    assert middleware.scorer_pause_state()["paused"] is False
    assert middleware.scorer_pause_state()["failures"] == 0


@pytest.mark.parametrize("code", [
    "timeout", "transport_error", "http_error", "malformed_response", "unexpected_error"])
def test_each_runtime_failure_code_counts(monkeypatch, clock, code):
    configure(monkeypatch, Scorer((None, code)))
    trip()
    assert middleware.scorer_pause_state()["paused"] is True


def test_success_resets_the_failure_count(monkeypatch, clock):
    scorer = Scorer(FAIL, FAIL, OK, FAIL, FAIL, FAIL)
    configure(monkeypatch, scorer)
    for i in range(5):
        call(f"t{i}")
    assert middleware.scorer_pause_state() == {
        "provider": "jev", "failures": 2, "paused": False, "remaining_s": 0}
    call("t5")
    assert middleware.scorer_pause_state()["paused"] is True


def test_provider_switch_starts_from_a_clean_count(monkeypatch, clock):
    scorer = Scorer(FAIL)
    values = configure(monkeypatch, scorer)
    trip()
    assert middleware.scorer_pause_state()["paused"] is True
    values["scorer_provider"] = "openrouter"
    assert middleware.scorer_pause_state() == {
        "provider": "openrouter", "failures": 0, "paused": False, "remaining_s": 0}
    call("on-new-provider")
    assert scorer.calls == 4
    values["scorer_provider"] = "jev"
    assert middleware.scorer_pause_state()["failures"] == 0


def test_disabled_with_zero_threshold(monkeypatch, clock):
    scorer = Scorer(FAIL)
    configure(monkeypatch, scorer, scorer_pause_failures=0)
    for i in range(8):
        call(f"t{i}")
    assert scorer.calls == 8
    assert middleware.scorer_pause_state()["paused"] is False


def test_setting_parsing(monkeypatch):
    values = configure(monkeypatch, None)
    settings = middleware._settings()
    assert settings["scorer_pause_failures"] == 3 and settings["scorer_pause_s"] == 60.0
    values.update(scorer_pause_failures=-2, scorer_pause_s=0)
    settings = middleware._settings()
    assert settings["scorer_pause_failures"] == 3 and settings["scorer_pause_s"] == 60.0
    values.update(scorer_pause_failures="0", scorer_pause_s="5.5")
    settings = middleware._settings()
    assert settings["scorer_pause_failures"] == 0 and settings["scorer_pause_s"] == 5.5
    values.update(scorer_pause_failures="junk", scorer_pause_s="junk")
    settings = middleware._settings()
    assert settings["scorer_pause_failures"] == 3 and settings["scorer_pause_s"] == 60.0


def test_pause_does_not_pin_a_retained_route(monkeypatch, clock):
    scorer = Scorer(FAIL, FAIL, FAIL, OK)
    configure(monkeypatch, scorer, mode="once")
    # A failed route pin in one session is never retried by design, so trip the
    # breaker across three separate sessions.
    for i in range(3):
        assert call(f"t{i}", session=f"sess-{i}") is None
    assert scorer.calls == 3
    assert call("p1", session="fresh") is None
    assert scorer.calls == 3
    assert turn_entry("p1", session="fresh")["failure"] == "scorer_paused"
    pins = [e for e in middleware.session_state().values()
            if e.get("scope") == "session_route" and e.get("conversation_id") == "fresh"]
    assert all(e.get("failure") != "scorer_paused" for e in pins)
    assert all(e.get("state") != "failed" for e in pins)
    clock.now += 61
    result = call("p2", session="fresh")
    assert scorer.calls == 4
    assert result is not None and result["request"]["reasoning"]["effort"] == "high"


def test_pause_never_overwrites_an_existing_pin(monkeypatch, clock):
    scorer = Scorer(OK, FAIL)
    configure(monkeypatch, scorer, mode="once")
    first = call("a", session="keep")
    assert first["request"]["reasoning"]["effort"] == "high"
    # Trip the breaker elsewhere, then continue the pinned session on a new turn.
    for i in range(3):
        call(f"t{i}", session=f"other-{i}")
    calls = scorer.calls
    again = call("b", session="keep")
    assert scorer.calls == calls
    assert again is not None and again["request"]["reasoning"]["effort"] == "high"


def test_probe_bypasses_the_pause_and_does_not_count(monkeypatch, clock):
    scorer = Scorer(FAIL)
    configure(monkeypatch, scorer)
    for _ in range(5):
        middleware.run_probe("some operator text")
    assert scorer.calls == 5
    assert middleware.scorer_pause_state()["failures"] == 0
    trip()
    assert middleware.scorer_pause_state()["paused"] is True
    before = scorer.calls
    result = middleware.run_probe("some operator text")
    assert scorer.calls == before + 1
    assert result["failure"] == "timeout"
    assert middleware.scorer_pause_state()["failures"] == 3


def test_subagent_shares_the_parents_pause(monkeypatch, clock):
    scorer = Scorer(FAIL)
    configure(monkeypatch, scorer)
    trip()
    calls = scorer.calls
    middleware.on_subagent_start(
        parent_session_id="s1", child_session_id="child-1",
        child_goal="Rename get_user_id to load_user_id in 12 files.")
    result = middleware.on_llm_request(
        request=body(), session_id="child-1", turn_id="c1", provider=PROVIDER, model=MODEL,
        api_mode=API)
    assert result is None
    assert scorer.calls == calls
    assert turn_entry("c1", session="child-1")["failure"] == "scorer_paused"


def test_status_text_and_json_report_the_pause(monkeypatch, clock):
    configure(monkeypatch, Scorer(FAIL))
    payload = json.loads(command.handle("status json"))
    assert payload["schema"] == "hermes-adaptive-effort.status.v2"
    assert payload["scorer_pause"] == {
        "provider": "jev", "failures": 0, "paused": False, "remaining_s": 0}
    assert "Scorer paused" not in command.handle("status")
    trip()
    clock.now += 20
    payload = json.loads(command.handle("status json"))
    assert payload["scorer_pause"] == {
        "provider": "jev", "failures": 3, "paused": True, "remaining_s": 40}
    assert "Scorer paused: 40 s left (3 failures)" in command.handle("status")
    clock.now += 100
    assert "Scorer paused" not in command.handle("status")
    assert json.loads(command.handle("status json"))["scorer_pause"]["paused"] is False
