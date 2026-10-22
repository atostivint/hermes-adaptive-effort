"""Operator bounds ``effort_floor`` / ``effort_ceiling`` (issue #85)."""

from __future__ import annotations

import pytest

from conftest import import_plugin

effort = import_plugin("effort")
middleware = import_plugin("middleware")

SESSION = "BOUNDS-SESSION"


class ChoiceScorer:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def classify_effort_detail(self, prompt, choices):
        self.calls.append(tuple(choices))
        return self.value, None

    def classify_detail(self, prompt):
        self.calls.append(None)
        return self.value, None


def configure(monkeypatch, scorer, **settings):
    values = {"mode": "always", **settings}
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kwargs: scorer)


def applied_effort(provider, model, api_mode, field, initial, turn="turn-1"):
    body = {"model": model, "messages": [{"role": "user", "content": "task"}],
            "reasoning_effort": initial}
    result = middleware.on_llm_request(
        request=body, session_id=SESSION, turn_id=turn,
        provider=provider, model=model, api_mode=api_mode)
    return None if result is None else result["request"][field]


@pytest.mark.parametrize("value", ["", None, "off", "none", "OFF", " None ", "ultra", "bogus", 3,
                                   True])
def test_a_bound_that_is_off_or_unrecognised_means_no_bound(value):
    assert effort.normalize_bound(value) is None


@pytest.mark.parametrize("value", ["minimal", "low", "medium", "high", "xhigh", "max", " High "])
def test_named_levels_are_valid_bounds(value):
    assert effort.normalize_bound(value) == value.strip().lower()


def test_floor_above_ceiling_invalidates_both_bounds():
    assert effort.normalize_bounds("high", "low") == (None, None)
    assert effort.normalize_bounds("medium", "medium") == ("medium", "medium")
    assert effort.normalize_bounds("low", "") == ("low", None)
    assert effort.normalize_bounds("", "off") == (None, None)


def test_bound_label_clamps_into_the_range_and_passes_unbounded_labels_through():
    assert effort.bound_label("low", "medium", None) == "medium"
    assert effort.bound_label("high", None, "medium") == "medium"
    assert effort.bound_label("medium", "low", "high") == "medium"
    assert effort.bound_label("low", None, None) == "low"
    assert effort.bound_label(None, "low", "high") is None


def test_settings_default_to_no_bounds_and_ignore_invalid_values(monkeypatch):
    assert middleware.DEFAULTS["effort_floor"] == middleware.DEFAULTS["effort_ceiling"] == ""
    values = {}
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    settings = middleware._settings()
    assert (settings["effort_floor"], settings["effort_ceiling"]) == (None, None)
    values.update(effort_floor="medium", effort_ceiling="high")
    settings = middleware._settings()
    assert (settings["effort_floor"], settings["effort_ceiling"]) == ("medium", "high")
    values.update(effort_floor="high", effort_ceiling="low")
    settings = middleware._settings()
    assert (settings["effort_floor"], settings["effort_ceiling"]) == (None, None)
    values.update(effort_floor="off", effort_ceiling="none")
    settings = middleware._settings()
    assert (settings["effort_floor"], settings["effort_ceiling"]) == (None, None)


def test_floor_lifts_a_named_choice_and_the_ceiling_can_be_switched_off(monkeypatch):
    route = ("opencode-go", "mimo-v2.6-flash", "chat_completions", "reasoning_effort")
    scorer = ChoiceScorer("low")
    configure(monkeypatch, scorer, effort_floor="medium")
    assert applied_effort(*route, "high") == "medium"
    # The scorer still sees the route's full list: the answer is clamped, not the question.
    assert scorer.calls == [("low", "medium", "high")]
    middleware.reset_state()
    configure(monkeypatch, ChoiceScorer("low"), effort_floor="off")
    assert applied_effort(*route, "high") == "low"


def test_ceiling_caps_a_named_choice(monkeypatch):
    route = ("opencode-go", "mimo-v2.6-flash", "chat_completions", "reasoning_effort")
    configure(monkeypatch, ChoiceScorer("high"), effort_ceiling="medium")
    assert applied_effort(*route, "low") == "medium"
    middleware.reset_state()
    configure(monkeypatch, ChoiceScorer("high"), effort_ceiling="none")
    assert applied_effort(*route, "low") == "high"


def test_a_bound_the_route_lacks_resolves_to_the_nearest_allowed_level(monkeypatch):
    # Kimi K3 is low/high/max with medium rounding up to high.
    route = ("opencode-go", "kimi-k3", "chat_completions", "reasoning_effort")
    configure(monkeypatch, ChoiceScorer("low"), effort_floor="medium")
    assert applied_effort(*route, "max") == "high"


def test_bounds_clamp_the_numeric_score_label_on_unknown_vocabularies(monkeypatch):
    route = ("some-provider", "unlisted-model", "chat_completions", "reasoning_effort")
    scorer = ChoiceScorer(0.0)
    configure(monkeypatch, scorer, effort_floor="medium")
    result = applied_effort(*route, "high")
    assert scorer.calls == [None]
    assert result in (None, "medium")


def test_unbounded_behaviour_is_unchanged(monkeypatch):
    route = ("opencode-go", "mimo-v2.6-flash", "chat_completions", "reasoning_effort")
    configure(monkeypatch, ChoiceScorer("low"))
    assert applied_effort(*route, "high") == "low"


def test_ceiling_caps_tool_error_escalation():
    choices = ("low", "medium", "high")
    assert middleware._cap_to_ceiling("high", "low", choices, "medium") == "medium"
    assert middleware._cap_to_ceiling("medium", "low", choices, "medium") == "medium"
    assert middleware._cap_to_ceiling("high", "low", choices, None) == "high"
    # Never lowers the target when it already sits at or above the ceiling.
    assert middleware._cap_to_ceiling("high", "medium", choices, "low") == "medium"
