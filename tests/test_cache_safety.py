"""Transport cache-safety checks and the four effort-mode decision scopes.

The transport verdict is one input to the exact dynamic-capability registry.
`auto` uses per-turn decisions only when both route-level control support and
transport evidence are present; other modes select their scopes independently.
The transport distinction is empirical, not folklore:

* On ``codex_responses`` the effort is a top-level request field, never rendered
  into the prompt text, and ``prompt_cache_key`` is byte-identical for ``low``
  and ``high`` (both verified live against ``ResponsesApiTransport``). The prefix
  is unchanged, so per-turn routing costs nothing.
* On the Anthropic route the thinking configuration is documented to be rendered
  into the prompt and to invalidate message blocks whenever it changes. The
  anthropic transport carries ``thinking.budget_tokens`` rather than an effort
  level, so an effort rewrite there is not just cache-hostile, it is wrong.

So this table is a per-route property, kept next to the code that rewrites the
request, and every entry has a test that names the evidence it rests on.
"""

from __future__ import annotations

import threading

import pytest

from conftest import import_plugin, score_to_effort_choice

middleware = import_plugin("middleware")
cache = import_plugin("cache_safety")


class FakeClassifier:
    def __init__(self, score=1.0):
        self.score = score
        self.timeout = None
        self.calls: list = []

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        return self.score, None

    def classify_effort_detail(self, prompt, choices):
        score, failure = self.classify_detail(prompt)
        choice = score_to_effort_choice(score, choices)
        return (choice, None) if choice is not None else (None, failure or "malformed_response")


class ScoreSequence:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls: list = []

    def __call__(self, timeout=None):
        client = FakeClassifier()
        parent = self

        def classify_detail(prompt):
            parent.calls.append(prompt)
            return (parent.scores.pop(0) if parent.scores else 0.0), None

        def classify_effort_detail(prompt, choices):
            score, failure = classify_detail(prompt)
            choice = score_to_effort_choice(score, choices)
            return (choice, None) if choice is not None else (None, failure or "malformed_response")

        client.classify_detail = classify_detail
        client.classify_effort_detail = classify_effort_detail
        return client


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    middleware.reset_state()
    monkeypatch.setattr(middleware, "_settings_provider", None)
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    yield
    middleware.reset_state()


def use_settings(monkeypatch, **kw):
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: kw.get(key, default))


def codex_request(effort="medium"):
    return {"model": "gpt-5.6-terra",
            "messages": [{"role": "user", "content": "work"}],
            "reasoning": {"effort": effort, "summary": "auto"}}


def chat_request(effort="medium"):
    return {"model": "openrouter/x/y",
            "messages": [{"role": "user", "content": "work"}],
            "extra_body": {"reasoning": {"enabled": True, "effort": effort}}}


def ask(monkeypatch, request, turn, *, session="MAIN", provider="openai-codex",
        model="gpt-5.6-terra", api_mode="codex_responses"):
    return middleware.on_llm_request(
        request=request, session_id=session, provider=provider, model=model,
        api_mode=api_mode, task_id="t", turn_id=turn, api_request_id="r",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")


# ── the route table itself ───────────────────────────────────────────────────

def test_codex_responses_is_cache_safe():
    """Effort is a request field there, never prompt text (verified live)."""
    assert cache.effort_is_cache_safe(provider="openai-codex", model="gpt-5.6-terra",
                                      api_mode="codex_responses") is True


def test_chat_completions_route_is_cache_safe():
    """OpenAI-compatible routes carry effort as a body parameter, not prompt text."""
    assert cache.effort_is_cache_safe(provider="openrouter", model="openrouter/x/y",
                                      api_mode="chat_completions") is True


def test_anthropic_route_is_not_cache_safe():
    """Anthropic renders the thinking config into the prompt (documented)."""
    assert cache.effort_is_cache_safe(provider="anthropic", model="claude-opus-5",
                                      api_mode="anthropic_messages") is False


def test_unknown_route_is_not_cache_safe():
    """Unknown means unknown: default to NOT safe rather than gamble."""
    assert cache.effort_is_cache_safe(provider=None, model=None, api_mode=None) is False
    assert cache.effort_is_cache_safe(provider="who-knows", model="x",
                                      api_mode="something_new") is False


# ── the four modes and their decision scopes ─────────────────────────────────

def test_auto_is_dynamic_only_for_explicitly_verified_routes():
    assert middleware._dynamic_effort_route(
        "openai-codex", "gpt-6.1-sol", "codex_responses") is True
    assert middleware._dynamic_effort_route(
        "openai-codex", "gpt-5.6-terra", "codex_responses") is False
    assert middleware._dynamic_effort_route(
        "opencode-go", "deepseek-v4-pro", "chat_completions") is True
    assert middleware._dynamic_effort_route(
        "opencode-go", "mimo-v2.6-flash", "chat_completions") is True
    assert middleware._dynamic_effort_route(
        "opencode-go", "mimo-v2.6-flash", "codex_responses") is False
    assert middleware._dynamic_effort_route(
        "opencode-go", "unknown-go-model", "chat_completions") is False
    # An operator's field-support assertion permits insertion, not dynamic mode.
    assert middleware._dynamic_effort_route(
        "openrouter", "manual-model", "chat_completions") is False


def test_auto_reclassifies_each_turn_on_verified_dynamic_route(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    for turn, expected in (("t1", "low"), ("t2", "high")):
        out = ask(monkeypatch, codex_request(), turn, provider="openai-codex",
                  model="gpt-6.1-sol", api_mode="codex_responses")
        assert out["request"]["reasoning"]["effort"] == expected
    assert len(seq.calls) == 2


@pytest.mark.parametrize("mode", ["auto", "once", "always", "off"])
@pytest.mark.parametrize("route_kind", ["verified", "unknown"])
def test_four_modes_across_verified_and_unknown_routes(monkeypatch, mode, route_kind):
    use_settings(monkeypatch, mode=mode)
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)

    def read_effort(output):
        return middleware._effort_slot(output["request"])[2]

    if route_kind == "verified":
        provider, model, api_mode = "openai-codex", "gpt-6.1-sol", "codex_responses"
        request = codex_request("medium")
    else:
        provider, model, api_mode = "openrouter", "openrouter/x/y", "chat_completions"
        request = chat_request("medium")

    outputs = [
        ask(monkeypatch, request, turn, provider=provider, model=model, api_mode=api_mode)
        for turn in ("t1", "t2")
    ]
    if mode == "off":
        assert outputs == [None, None]
        assert seq.calls == []
        return

    values = [read_effort(output) for output in outputs]
    if mode == "once":
        assert values == ["low", "low"]
        assert len(seq.calls) == 1
    elif mode == "always" or (mode == "auto" and route_kind == "verified"):
        assert values == ["low", "high"]
        assert len(seq.calls) == 2
    else:
        assert values == ["low", "low"]
        assert len(seq.calls) == 1


def test_auto_pins_one_decision_per_unverified_route(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    seq = ScoreSequence([1.9, 0.1])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    for turn in ("t1", "t2"):
        out = ask(monkeypatch, chat_request(), turn, provider="openrouter",
                  model="openrouter/x/y", api_mode="chat_completions")
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(seq.calls) == 1
    out = ask(monkeypatch, chat_request(), "t3", provider="openrouter",
              model="openrouter/x/z", api_mode="chat_completions")
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert len(seq.calls) == 2


def test_once_reuses_route_pins_and_normalizes_provider_aliases(monkeypatch):
    use_settings(monkeypatch, mode="once")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    req = codex_request()
    for turn, provider in (("t1", "opencode"), ("t2", "opencode-zen")):
        out = ask(monkeypatch, req, turn, provider=provider,
                  model="muse-spark-1.3", api_mode="codex_responses")
        assert out["request"]["reasoning"]["effort"] == "low"
    assert len(seq.calls) == 1


def test_once_reevaluates_new_model_and_reuses_prior_model(monkeypatch):
    use_settings(monkeypatch, mode="once")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    for turn, model, expected in (
        ("t1", "model-a", "low"), ("t2", "model-b", "high"),
        ("t3", "model-a", "low"),
    ):
        out = ask(monkeypatch, chat_request(), turn, provider="openrouter",
                  model=model, api_mode="chat_completions")
        assert out["request"]["extra_body"]["reasoning"]["effort"] == expected
    assert len(seq.calls) == 2


def test_once_route_identity_includes_provider_and_api_mode(monkeypatch):
    use_settings(monkeypatch, mode="once")
    seq = ScoreSequence([0.1, 1.9, 1.0])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    routes = (
        ("t1", "openrouter", "chat_completions", "low"),
        ("t2", "custom-endpoint", "chat_completions", "high"),
        ("t3", "openrouter", "anthropic_messages", "medium"),
        ("t4", "openrouter", "chat_completions", "low"),
    )
    for turn, provider, api_mode, expected in routes:
        out = ask(monkeypatch, chat_request("max"), turn, provider=provider,
                  model="openrouter/x/y", api_mode=api_mode)
        assert out["request"]["extra_body"]["reasoning"]["effort"] == expected
    assert len(seq.calls) == 3


def test_route_change_inside_once_turn_prefers_this_turn_label_over_old_pin(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    seq = ScoreSequence([0.1, 1.0])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)

    # Retain a low decision for Kimi K3, then classify a later task dynamically
    # on Codex. Returning to K3 in that same tool loop must re-clamp this turn's
    # medium label to K3's high tier rather than replaying its older low pin.
    first = ask(monkeypatch, chat_request("medium"), "t0", provider="openrouter",
                model="moonshot/kimi-k3", api_mode="chat_completions")
    assert first["request"]["extra_body"]["reasoning"]["effort"] == "low"
    ask(monkeypatch, codex_request("low"), "t1", provider="openai-codex",
        model="gpt-6.1-sol", api_mode="codex_responses")
    returned = ask(monkeypatch, chat_request("low"), "t1", provider="openrouter",
                    model="moonshot/kimi-k3", api_mode="chat_completions")
    assert returned["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(seq.calls) == 2

    # A later turn returns to the retained route decision from t0.
    next_turn = ask(monkeypatch, chat_request("medium"), "t2", provider="openrouter",
                    model="moonshot/kimi-k3", api_mode="chat_completions")
    assert next_turn["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert len(seq.calls) == 2


def test_reset_and_new_session_start_independent_once_decisions(monkeypatch):
    use_settings(monkeypatch, mode="once")
    seq = ScoreSequence([0.1, 1.9, 1.0])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    first = ask(monkeypatch, chat_request("max"), "t1", session="SESSION-A")
    other_session = ask(monkeypatch, chat_request("max"), "t1", session="SESSION-B")
    assert first["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert other_session["request"]["extra_body"]["reasoning"]["effort"] == "high"
    middleware.reset_state()
    after_reset = ask(monkeypatch, chat_request("max"), "t2", session="SESSION-A")
    assert after_reset["request"]["extra_body"]["reasoning"]["effort"] == "medium"
    assert len(seq.calls) == 3


def test_turn_and_route_ledgers_have_separate_max_turns_bounds(monkeypatch):
    use_settings(monkeypatch, mode="once", max_turns=1)
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    ask(monkeypatch, chat_request(), "t1", model="model-a")
    ask(monkeypatch, chat_request(), "t2", model="model-b")
    state = middleware.session_state()
    assert sum(entry.get("scope") == "turn" for entry in state.values()) == 1
    assert sum(entry.get("scope") == "session_route" for entry in state.values()) == 1
    assert len(seq.calls) == 2


def test_concurrent_route_change_cannot_make_second_scorer_call(monkeypatch):
    use_settings(monkeypatch, mode="auto")
    started = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingClassifier:
        def classify_detail(self, prompt):
            calls.append(prompt)
            started.set()
            assert release.wait(5)
            return 1.0, None

        def classify_effort_detail(self, prompt, choices):
            score, failure = self.classify_detail(prompt)
            choice = score_to_effort_choice(score, choices)
            return (choice, None) if choice is not None else (None, failure or "malformed_response")

    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: BlockingClassifier())
    results = []
    worker = threading.Thread(target=lambda: results.append(
        ask(monkeypatch, chat_request("low"), "shared-turn", model="model-a")))
    worker.start()
    assert started.wait(5)
    # A different persistent route overlaps the in-flight turn classification.
    assert ask(monkeypatch, chat_request("low"), "shared-turn", model="model-b") is None
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(calls) == 1
    # Once the turn decision exists, the other route reuses and re-clamps it.
    reused = ask(monkeypatch, chat_request("low"), "shared-turn", model="model-b")
    assert reused["request"]["extra_body"]["reasoning"]["effort"] == "medium"
    assert len(calls) == 1


def test_concurrent_turns_cannot_classify_the_same_persistent_route_twice(monkeypatch):
    use_settings(monkeypatch, mode="once")
    started = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingClassifier:
        def classify_detail(self, prompt):
            calls.append(prompt)
            started.set()
            assert release.wait(5)
            return 1.9, None

        def classify_effort_detail(self, prompt, choices):
            score, failure = self.classify_detail(prompt)
            choice = score_to_effort_choice(score, choices)
            return (choice, None) if choice is not None else (None, failure or "malformed_response")

    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: BlockingClassifier())
    results = []
    worker = threading.Thread(target=lambda: results.append(
        ask(monkeypatch, chat_request(), "t1", model="same-model")))
    worker.start()
    assert started.wait(5)
    assert ask(monkeypatch, chat_request(), "t2", model="same-model") is None
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(calls) == 1
    second_turn = ask(monkeypatch, chat_request(), "t2", model="same-model")
    assert second_turn["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert len(calls) == 1


def test_always_reclassifies_each_message_but_reuses_tool_loop(monkeypatch):
    use_settings(monkeypatch, mode="always")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    for turn, expected in (("t1", "low"), ("t2", "high"), ("t2", "high")):
        out = ask(monkeypatch, chat_request(), turn, provider="anthropic",
                  model="claude-opus-5", api_mode="chat_completions")
        assert out["request"]["extra_body"]["reasoning"]["effort"] == expected
    assert len(seq.calls) == 2


def test_off_routes_nothing_anywhere(monkeypatch):
    use_settings(monkeypatch, mode="off")
    seq = ScoreSequence([1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    assert ask(monkeypatch, codex_request(), "t1") is None
    assert seq.calls == []


def test_child_modes_use_their_independent_gate(monkeypatch):
    use_settings(monkeypatch, mode="auto", subagent_mode="once")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    middleware.on_subagent_start(parent_session_id="P", child_session_id="C",
                                 child_goal="mechanical rename across 12 files")
    for turn in ("t1", "t2"):
        out = ask(monkeypatch, codex_request(), turn, session="C")
        assert out["request"]["reasoning"]["effort"] == "low"
    assert len(seq.calls) == 1


def test_effort_unchanged_returns_no_decision(monkeypatch):
    use_settings(monkeypatch, mode="always")
    seq = ScoreSequence([0.1])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    out = ask(monkeypatch, codex_request(effort="low"), "t1",
              provider="openai-codex", model="gpt-6.1-sol", api_mode="codex_responses")
    assert out is None
