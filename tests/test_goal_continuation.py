"""Goal and kanban continuation escalation; fakes only, network-free."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

middleware = import_plugin("middleware")
goal_loop = import_plugin("goal_loop")
command = import_plugin("command")

SESSION = "GOAL"
GO = ("opencode-go", "glm-5.3", "chat_completions")
OPENROUTER = ("openrouter", "x/y", "chat_completions")

GOAL_PROMPT = (
    "[Continuing toward your standing goal]\n"
    "Goal: ship the feature\n\n"
    "Continue working toward this goal. Take the next concrete step."
)
GOAL_GATE_PROMPT = (
    "[Continuing toward your standing goal \u2014 a quality gate failed]\n"
    "Goal: ship the feature\n\n"
    "The quality gate command below must pass before this goal can be declared done."
)
KANBAN_PROMPT = (
    "[Continuing toward this kanban task \u2014 judge says it is not done yet]\n"
    "Reason: tests are failing\n\n"
    "Take the next concrete step toward completing the task."
)


class ChoiceScorer:
    def __init__(self, value="low"):
        self.value = value
        self.calls = []

    def classify_effort_detail(self, prompt, choices):
        self.calls.append(tuple(choices))
        return self.value, None

    def classify_detail(self, prompt):
        self.calls.append(None)
        return 1.0, None


def configure(monkeypatch, scorer=None, **values):
    values.setdefault("mode", "always")
    values.setdefault("goal_continuation_escalation", True)
    values.setdefault("tool_error_escalation", False)
    values.setdefault("dialogue_context_messages", 0)
    values.setdefault("respect_manual_effort", False)
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    if scorer is not None:
        monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kwargs: scorer)


def send(text, turn="t1", effort="low", route=GO):
    provider, model, api_mode = route
    request = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "reasoning_effort": effort,
    }
    out = middleware.on_llm_request(
        request=request, session_id=SESSION, turn_id=turn, provider=provider, model=model,
        api_mode=api_mode)
    return out["request"]["reasoning_effort"] if out else None


def entry(turn):
    return middleware.session_state()[f"{SESSION}/{turn}"]


@pytest.fixture(autouse=True)
def clean():
    middleware.reset_state()
    yield
    middleware.reset_state()


def test_goal_loop_markers():
    assert goal_loop.is_goal_continuation(GOAL_PROMPT)
    assert goal_loop.is_goal_continuation(GOAL_GATE_PROMPT)
    assert goal_loop.is_goal_continuation(KANBAN_PROMPT)
    assert goal_loop.is_goal_continuation("  " + GOAL_PROMPT)
    assert not goal_loop.is_goal_continuation(
        "[Goal set] Continue with the goal you were just given; there is no need to re-read it.")
    assert not goal_loop.is_goal_continuation(
        "[The work looks complete, but the task is still open]\nReason: done")
    assert not goal_loop.is_goal_continuation("fix the failing test")
    assert not goal_loop.is_goal_continuation(None)


def test_goal_continuation_raises_one_level_above_the_previous_turn(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1")
    assert entry("t1")["target"] == "low"
    assert send(GOAL_PROMPT, turn="t2") == "high"
    assert entry("t2")["target"] == "low"          # the scorer's decision
    assert entry("t2")["_applied_target"] == "high"  # the level sent for this retry
    assert entry("t2")["escalated"] is True
    assert entry("t2")["escalation_reason"] == "goal_continuation"


def test_a_second_goal_continuation_raises_again(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1")
    assert send(GOAL_PROMPT, turn="t2") == "high"
    assert send(GOAL_PROMPT, turn="t3") == "max"  # low -> high -> max


def test_kanban_continuation_raises_one_level(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1")
    assert send(KANBAN_PROMPT, turn="t2") == "high"
    assert entry("t2")["escalation_reason"] == "goal_continuation"


def test_a_normal_user_message_does_not_raise(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1")
    send("now write the documentation", turn="t2")
    assert entry("t2")["target"] == "low"
    assert entry("t2")["escalated"] is False


def test_setting_off_does_not_raise(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer, goal_continuation_escalation=False)
    send("fix the failing test", turn="t1")
    send(GOAL_PROMPT, turn="t2")
    assert entry("t2")["target"] == "low"
    assert entry("t2")["escalated"] is False


def test_once_mode_does_not_raise(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer, mode="once")
    send("fix the failing test", turn="t1")
    send(GOAL_PROMPT, turn="t2")
    assert entry("t2")["target"] == "low"
    assert entry("t2")["escalated"] is False


def test_non_dynamic_route_does_not_raise(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1", route=OPENROUTER)
    send(GOAL_PROMPT, turn="t2", route=OPENROUTER)
    assert entry("t2")["escalated"] is False
    assert entry("t2")["escalation_reason"] is None


def test_ceiling_caps_the_goal_raise(monkeypatch):
    scorer = ChoiceScorer("high")
    configure(monkeypatch, scorer, effort_ceiling="high")
    send("fix the failing test", turn="t1", effort="high")
    assert entry("t1")["target"] == "high"
    scorer.value = "low"
    assert send(GOAL_PROMPT, turn="t2", effort="low") == "high"  # max next, capped to high
    assert entry("t2")["_applied_target"] == "high"


def test_top_of_the_route_is_not_exceeded(monkeypatch):
    scorer = ChoiceScorer("max")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1", effort="max")
    assert send(GOAL_PROMPT, turn="t2", effort="max") is None  # already max: no rewrite
    assert entry("t2")["target"] == "max"
    assert entry("t2")["escalated"] is False


def test_goal_text_is_not_exposed(monkeypatch):
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer)
    send("fix the failing test", turn="t1")
    send(GOAL_PROMPT.replace("ship the feature", "SECRET-GOAL-TEXT"), turn="t2")
    rendered = (json.dumps(command._status_payload()) + command.handle("status")
                + json.dumps(middleware.effort_change_state()))
    assert "SECRET-GOAL-TEXT" not in rendered
    assert "Continuing toward your standing goal" not in rendered
