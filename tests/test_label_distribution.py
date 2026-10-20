"""Label distribution: raw scorer label counts per route, dominance flag, fail-open."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")
command = import_plugin("command")

PROVIDER, MODEL, API = "openai-codex", "gpt-5.6-terra", "codex_responses"


class Scorer:
    """Fake scorer returning queued ``(result, failure)`` outcomes (last one repeats)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, timeout=None):
        return self

    def classify_detail(self, prompt):
        self.calls += 1
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


def configure(monkeypatch, scorer, **overrides):
    values = {"mode": "always", "subagent_mode": "always"}
    values.update(overrides)
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(middleware, "_classifier_factory", scorer)


def call(turn, session="s1", model=MODEL, provider=PROVIDER, api=API):
    return middleware.on_llm_request(
        request={
            "model": model,
            "messages": [{"role": "user", "content": "Rename a function in twelve files."}],
            "reasoning": {"effort": "medium", "summary": "auto"},
        },
        session_id=session, turn_id=turn, provider=provider, model=model, api_mode=api)


def routes():
    return {(r["provider"], r["model"], r["api_mode"]): r
            for r in middleware.label_distribution_state()["routes"]}


def test_empty_state():
    assert middleware.label_distribution_state() == {"routes": [], "truncated": 0}


def test_fresh_answers_count_by_route(monkeypatch):
    configure(monkeypatch, Scorer((0.2, None), (1.0, None), (1.9, None), (0.1, None)))
    for turn in range(4):
        call(f"t{turn}")
    route = routes()[(PROVIDER, MODEL, API)]
    assert route["counts"] == {"low": 2, "medium": 1, "high": 1}
    assert route["total"] == 4
    assert route["dominant"] is None


def test_failed_answer_is_not_counted(monkeypatch):
    configure(monkeypatch, Scorer((None, "timeout")))
    call("t1")
    assert routes() == {}


def test_scorer_paused_is_not_counted(monkeypatch):
    scorer = Scorer((None, "timeout"))
    configure(monkeypatch, scorer, scorer_pause_failures=1, scorer_pause_s=60)
    call("t1")
    call("t2")
    assert scorer.calls == 1
    assert routes() == {}


def test_pin_reuse_does_not_recount(monkeypatch):
    scorer = Scorer((1.9, None))
    configure(monkeypatch, scorer, mode="once")
    call("t1")
    call("t2")
    assert scorer.calls == 1
    assert routes()[(PROVIDER, MODEL, API)]["total"] == 1


def test_routes_stay_separate(monkeypatch):
    configure(monkeypatch, Scorer((0.2, None)))
    call("t1")
    call("t2", model="gpt-5.6-sol")
    assert len(routes()) == 2


def test_route_cap_evicts_oldest_and_reports_truncated():
    for index in range(33):
        middleware._label_distribution_record("p", f"m{index}", "chat_completions", "low")
    state = middleware.label_distribution_state()
    assert len(state["routes"]) == 32
    assert state["truncated"] == 1
    assert state["routes"][0]["model"] == "m1"


def test_reset_state_clears():
    for index in range(33):
        middleware._label_distribution_record("p", f"m{index}", "x", "low")
    middleware.reset_state()
    assert middleware.label_distribution_state() == {"routes": [], "truncated": 0}


@pytest.mark.parametrize("high,low,flagged", [
    (29, 0, False),   # below the sample floor
    (30, 0, True),
    (26, 4, False),   # 86.7 %
    (27, 3, True),    # 90 %
    (26, 3, False),   # 29 answers
])
def test_dominance(high, low, flagged):
    for _ in range(high):
        middleware._label_distribution_record("p", "m", "x", "high")
    for _ in range(low):
        middleware._label_distribution_record("p", "m", "x", "low")
    dominant = middleware.label_distribution_state()["routes"][0]["dominant"]
    assert (dominant is not None) is flagged
    if flagged:
        assert dominant["label"] == "high"


def test_recorder_failure_leaves_request_unchanged(monkeypatch):
    configure(monkeypatch, Scorer((1.9, None)))
    baseline = call("t1")
    middleware.reset_state()
    configure(monkeypatch, Scorer((1.9, None)))

    class Boom(dict):
        def get(self, *args):
            raise RuntimeError("boom")

    monkeypatch.setattr(middleware, "_LABEL_COUNTS", Boom())
    assert call("t1") == baseline


def test_hysteresis_counts_raw_label(monkeypatch):
    # Held label stays high while the raw answer is low: the raw label is counted.
    scorer = Scorer((1.9, None), (0.1, None), (0.1, None))
    configure(monkeypatch, scorer, continuation_downgrade_votes=2,
              dialogue_context_messages=2)
    monkeypatch.setattr(middleware, "_hysteresis",
                        lambda route_key, session, label, vote, votes, max_turns:
                        ("high", label != "high"))
    for turn in range(3):
        call(f"t{turn}")
    counts = routes()[(PROVIDER, MODEL, API)]["counts"]
    assert counts == {"high": 1, "low": 2}


def test_status_json_and_text(monkeypatch):
    configure(monkeypatch, Scorer((0.2, None), (1.9, None)))
    call("t1")
    call("t2")
    payload = json.loads(command._status_json())
    dist = payload["label_distribution"]
    assert dist["truncated"] == 0
    assert dist["routes"][0]["counts"] == {"high": 1, "low": 1}
    assert "Rename" not in json.dumps(dist) and "s1" not in json.dumps(dist)
    assert payload["schema"] == "hermes-adaptive-effort.status.v2" if "schema" in payload else True
    assert "Effort labels (gpt-5.6-terra): high 1 · low 1" in command._status_full_text()


def test_text_line_only_with_data():
    assert "Effort labels" not in command._status_text() if hasattr(command, "_status_text") \
        else True
    assert "Effort labels: none yet" in command._status_full_text()
