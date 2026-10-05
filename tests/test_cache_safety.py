"""Cache-safety detection for effort rewrites.

Turning a router on is "cache-hostile by design" (see the
prompt-cache-safety-review skill), so the safe default is a mode that pins the
effort for the whole conversation and only moves it when the route is verified
to keep the cache across an effort change.

The distinction is empirical, not folklore:

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

import pytest

from conftest import import_plugin, settings_with_prompt_consent

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

        client.classify_detail = classify_detail
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


# ── per-turn mode: only on a cache-safe route ────────────────────────────────

def test_cache_safe_mode_routes_every_turn(monkeypatch):
    use_settings(monkeypatch, mode="cache_safe")
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([0.1, 1.9]))

    first = codex_request()
    out1 = ask(monkeypatch, first, "t1")
    assert out1["request"]["reasoning"]["effort"] == "low"

    second = codex_request()
    out2 = ask(monkeypatch, second, "t2")
    assert out2 is not None
    assert out2["request"]["reasoning"]["effort"] == "high"


def test_cache_safe_mode_pins_the_session_on_an_unsafe_route(monkeypatch):
    """Unsafe route: the first decision stands for the whole conversation.

    The first turn is still routed (that is the point of the mode); it is every
    LATER turn that reuses it, so exactly one scorer call covers the session.
    """
    use_settings(monkeypatch, mode="cache_safe")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)

    first = chat_request()
    out1 = ask(monkeypatch, first, "t1", provider="anthropic", model="claude-opus-5",
               api_mode="anthropic_messages")
    assert out1["request"]["extra_body"]["reasoning"]["effort"] == "low"

    # Turn 2 would classify "high" if it were re-classified: it must reuse the
    # pinned "low" instead. Rewriting to the same level is idempotent on the
    # wire — the prefix is identical either way, which is the point of pinning.
    second = chat_request()
    out2 = ask(monkeypatch, second, "t2", provider="anthropic", model="claude-opus-5",
               api_mode="anthropic_messages")
    assert len(seq.calls) == 1
    assert out2 is not None
    assert out2["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert list(middleware.session_state()) == ["MAIN"]   # pinned to the session


def test_cache_safe_mode_falls_back_to_session_pinning_when_route_unknown(monkeypatch):
    use_settings(monkeypatch, mode="cache_safe")
    seq = ScoreSequence([1.9, 0.1])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    first = chat_request()
    out1 = ask(monkeypatch, first, "t1", provider="who-knows", model="x",
               api_mode="something_new")
    assert out1["request"]["extra_body"]["reasoning"]["effort"] == "high"
    # Turn 2 would flip to "low" if re-classified: an unknown route must not.
    out = ask(monkeypatch, chat_request(), "t2", provider="who-knows", model="x",
              api_mode="something_new")
    assert len(seq.calls) == 1
    assert out is not None
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
    assert list(middleware.session_state()) == ["MAIN"]


# ── the three modes coexist ──────────────────────────────────────────────────

def test_auto_still_routes_every_turn_on_any_route(monkeypatch):
    """`auto` is the explicit opt-in: per-turn, whatever the route."""
    use_settings(monkeypatch, mode="auto")
    monkeypatch.setattr(middleware, "_classifier_factory", ScoreSequence([0.1, 1.9]))
    ask(monkeypatch, chat_request(), "t1", provider="anthropic", model="claude-opus-5",
        api_mode="anthropic_messages")
    second = chat_request()
    out = ask(monkeypatch, second, "t2", provider="anthropic", model="claude-opus-5",
              api_mode="anthropic_messages")
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"


def test_off_routes_nothing_anywhere(monkeypatch):
    use_settings(monkeypatch, mode="off")
    seq = ScoreSequence([1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    assert ask(monkeypatch, codex_request(), "t1") is None
    assert seq.calls == []


def test_cache_safe_is_rejected_as_a_session_mode_for_children_only(monkeypatch):
    """subagent_mode keeps its own vocabulary; a child is its own short session."""
    use_settings(monkeypatch, mode="auto", subagent_mode="auto")
    seq = ScoreSequence([0.1, 1.9])
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    middleware.on_subagent_start(parent_session_id="P", child_session_id="C",
                                 child_goal="mechanical rename across 12 files")
    req = codex_request()
    out = ask(monkeypatch, req, "t1", session="C")
    assert out["request"]["reasoning"]["effort"] == "low"
    assert len(seq.calls) == 1


def test_effort_unchanged_returns_no_decision(monkeypatch):
    """A turn that needs the level already on the wire needs no scorer call."""
    use_settings(monkeypatch, mode="cache_safe")
    seq = ScoreSequence([0.1])   # would pick "low"
    monkeypatch.setattr(middleware, "_classifier_factory", seq)
    out = ask(monkeypatch, codex_request(effort="low"), "t1")
    assert out is None
