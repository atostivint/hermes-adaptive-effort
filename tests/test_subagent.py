"""Subagent effort routing: identify a child request, classify the PARENT's goal.

Two facts drive this module, both read out of the installed source:

* ``subagent_start`` fires with ``parent_session_id``, ``child_session_id`` and
  ``child_goal`` (``tools/delegate_tool.py``) BEFORE the child's first request —
  the hook is invoked at the end of child construction, while the turn itself is
  submitted later from ``delegate_tool_child_run.py``. So nothing is missed.
* A child is its own ``AIAgent`` with its own ``session_id``, so the parent and
  the child never share a decision key.

The child request's own first user message is the goal the parent wrote, but the
hook hands that text over verbatim, so the classifier never parses a prompt.
"""

from __future__ import annotations

import pytest

from conftest import import_plugin, settings_with_prompt_consent

middleware = import_plugin("middleware")


class FakeClassifier:
    def __init__(self, score=1.9, error=None):
        self.score = score
        self.error = error
        self.timeout = None
        self.calls: list = []

    def classify_detail(self, prompt):
        # The production path prefers classify_detail(); record there so the
        # assertion below observes the text the classifier actually received.
        self.calls.append(prompt)
        if self.error is not None:
            return None, "classifier_error"
        return self.score, None

    def classify(self, prompt):
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return self.score


class Factory:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.instances = []

    @property
    def calls(self):
        return [call for inst in self.instances for call in inst.calls]

    def __call__(self, timeout=None):
        client = FakeClassifier(**self.kwargs)
        client.timeout = timeout
        self.instances.append(client)
        return client


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    middleware.reset_state()
    monkeypatch.setattr(middleware, "_settings_provider", None)
    monkeypatch.setattr(middleware, "_classifier_factory", None)
    monkeypatch.setattr(middleware, "subagent_mode", "off", raising=False)
    yield
    middleware.reset_state()


def use_settings(monkeypatch, settings):
    settings = settings_with_prompt_consent(settings)
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: settings.get(key, default))


def use_classifier(monkeypatch, factory):
    monkeypatch.setattr(middleware, "_classifier_factory", factory)


def child_request(**extra):
    """A child's recorded first request: the goal the parent wrote."""
    req = {
        "model": "gpt-5.6-terra",
        "messages": [
            {"role": "system", "content": "You are a subagent."},
            {"role": "user", "content": "Rename get_user_id to load_user_id in 12 files."},
        ],
        "reasoning": {"effort": "medium", "summary": "auto"},
    }
    req.update(extra)
    return req


def ctx(session="child-1", provider="openai-codex", model="gpt-5.6-terra", **extra):
    kw = {"request": None, "session_id": session, "provider": provider, "model": model,
          "task_id": "t", "turn_id": "turn", "api_request_id": "r1",
          "api_mode": "codex_responses",
          "middleware_schema_version": "hermes.middleware.v1"}
    kw.update(extra)
    return kw


def start_child(child_session_id="child-1", goal="Rename get_user_id to load_user_id in 12 files.",
                parent="parent-1"):
    return middleware.on_subagent_start(
        parent_session_id=parent, child_session_id=child_session_id, child_goal=goal)


def stop_child(child_session_id="child-1", parent="parent-1"):
    return middleware.on_subagent_stop(
        parent_session_id=parent, child_session_id=child_session_id)


# ── the third effort shape: top-level reasoning.effort (codex_responses) ──────

def test_top_level_reasoning_effort_is_a_writable_slot():
    """codex_responses puts effort top-level; verified live against the real transport."""
    container, key, old = middleware._effort_slot(child_request())
    assert (container["effort"], key, old) == ("medium", "effort", "medium")


def test_top_level_reasoning_never_rewrites_a_disabled_route():
    assert middleware._effort_slot(child_request(reasoning={"effort": "none"})) is None
    assert middleware._effort_slot(child_request(reasoning={})) is None


# ── registry ─────────────────────────────────────────────────────────────────

def test_subagent_start_records_child_and_goal():
    start_child()
    assert middleware.child_goals() == {
        "child-1": "Rename get_user_id to load_user_id in 12 files."}


def test_subagent_start_without_child_id_is_ignored():
    middleware.on_subagent_start(parent_session_id="p", child_session_id=None, child_goal="x")
    assert middleware.child_goals() == {}


def test_subagent_start_without_goal_is_not_registered():
    """No goal means nothing to classify; never fall back to the child's prompt."""
    start_child(goal="")
    assert middleware.child_goals() == {}


def test_subagent_stop_forgets_the_child():
    start_child()
    stop_child()
    assert middleware.child_goals() == {}


def test_registry_is_bounded_fifo(monkeypatch):
    use_settings(monkeypatch, {"max_turns": 3})
    for index in range(6):
        start_child(child_session_id=f"c{index}", goal=f"goal {index}")
    keys = list(middleware.child_goals())
    assert len(keys) == 3
    assert keys == ["c3", "c4", "c5"]  # oldest evicted


def test_session_end_clears_only_that_session(monkeypatch):
    start_child(child_session_id="c1", parent="p1")
    start_child(child_session_id="c2", parent="p1")
    middleware.on_session_end(session_id="c1")
    assert list(middleware.child_goals()) == ["c2"]


# ── the child path ───────────────────────────────────────────────────────────

def test_child_default_off_never_classifies(monkeypatch):
    use_classifier(monkeypatch, Factory())
    req = child_request()
    assert middleware.on_llm_request(**ctx(request=req)) is None
    assert req["reasoning"]["effort"] == "medium"


def test_child_auto_rewrites_from_the_parent_goal(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "auto"})
    factory = Factory(score=1.9)
    use_classifier(monkeypatch, factory)
    start_child()
    out = middleware.on_llm_request(**ctx(request=child_request()))
    assert out is not None
    assert out["request"]["reasoning"]["effort"] == "high"
    # The classified text is the parent's goal, verbatim.
    assert factory.instances[0].calls == [
        "Rename get_user_id to load_user_id in 12 files."]


def test_child_recommend_does_not_mutate(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "recommend"})
    use_classifier(monkeypatch, Factory(score=1.9))
    start_child()
    out = middleware.on_llm_request(**ctx(request=child_request()))
    assert out is not None
    assert out["request"]["reasoning"]["effort"] == "medium"
    assert "not applied" in out["reason"]


def test_subagent_mode_off_leaves_children_alone(monkeypatch):
    """Session auto + subagent off: the parent session is routed, children are not."""
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "off"})
    factory = Factory(score=1.9)
    use_classifier(monkeypatch, factory)
    start_child()
    req = child_request()
    assert middleware.on_llm_request(**ctx(request=req)) is None
    assert req["reasoning"]["effort"] == "medium"
    assert factory.instances == []


def test_parent_session_is_unaffected_by_subagent_mode(monkeypatch):
    """subagent_mode only gates children: the parent keeps the session's own mode."""
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "off"})
    use_classifier(monkeypatch, Factory(score=0.2))
    req = {
        "model": "openrouter/x/y",
        "messages": [{"role": "user", "content": "Design a failover plan."}],
        "extra_body": {"reasoning": {"enabled": True, "effort": "medium"}},
    }
    out = middleware.on_llm_request(**ctx(
        session="parent-1", provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", request=req))
    assert out is not None
    assert out["request"]["extra_body"]["reasoning"]["effort"] == "low"


def test_one_jev_call_per_child_not_per_request(monkeypatch):
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "auto"})
    factory = Factory(score=1.9)
    use_classifier(monkeypatch, factory)
    start_child()
    for _ in range(4):
        out = middleware.on_llm_request(**ctx(request=child_request()))
        assert out["request"]["reasoning"]["effort"] == "high"
    assert len(factory.instances) == 1


def test_unregistered_session_falls_back_to_its_own_prompt(monkeypatch):
    """A session we never saw as a child is treated as a normal session."""
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "auto"})
    use_classifier(monkeypatch, Factory(score=0.2))
    out = middleware.on_llm_request(**ctx(session="unknown", request=child_request()))
    assert out is not None
    assert out["request"]["reasoning"]["effort"] == "low"


def test_stale_child_registration_is_evicted_by_bound(monkeypatch):
    """A child that never reports stop is bounded, not leaked forever."""
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "auto", "max_turns": 2})
    use_classifier(monkeypatch, Factory(score=1.9))
    for index in range(4):
        start_child(child_session_id=f"c{index}")
    assert len(middleware.child_goals()) == 2


def test_no_prompt_text_is_stored_beyond_the_parent_goal(monkeypatch):
    """The registry holds the goal the parent wrote, and nothing from the child."""
    use_settings(monkeypatch, {"mode": "auto", "subagent_mode": "auto"})
    use_classifier(monkeypatch, Factory(score=1.9))
    start_child(goal="mechanical rename")
    middleware.on_llm_request(**ctx(request=child_request()))
    for _key, value in middleware.child_goals().items():
        assert value == "mechanical rename"
