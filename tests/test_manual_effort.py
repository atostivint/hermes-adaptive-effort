"""respect_manual_effort: the plugin stands down once the user sets the effort by hand.

Evidence for the comparison rules (``agent/turn_api_request.py:build_api_request``): Hermes
rebuilds the provider kwargs from agent state on EVERY attempt (``agent._build_api_kwargs``),
hands the middleware a copy, and never feeds the rewritten payload back. So the incoming
effort of one conversation is constant until the user changes it, whatever the plugin wrote;
only a Desktop selector sync can bring the plugin's own value back. Hence: manual means the
incoming value differs from BOTH the last one read and the last one applied.
"""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

history_store = import_plugin("history_store")
middleware = import_plugin("middleware")
command = import_plugin("command")
effort = import_plugin("effort")

CODEX = ("openai-codex", "gpt-6.1-sol", "codex_responses")
OTHER = ("openai-codex", "gpt-6.1-terra", "codex_responses")
SESSION = "conv-1"


class Scorer:
    """Counts scorer calls; answers a fixed named level."""

    def __init__(self, level="high"):
        self.level = level
        self.calls: list = []

    def __call__(self, timeout=None):
        parent = self

        class Client:
            timeout = None

            def classify_detail(self, prompt):
                parent.calls.append(prompt)
                return 1.9, None

            def classify_effort_detail(self, prompt, choices):
                parent.calls.append(prompt)
                return (parent.level if parent.level in choices else None), None

        return Client()


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    middleware.reset_state()
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: tmp_path / "h.sqlite3")
    yield
    middleware.reset_state()


def configure(monkeypatch, scorer, **extra):
    settings = {"mode": "auto", "subagent_mode": "auto", **extra}
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(middleware, "_classifier_factory", scorer)


def send(effort_value, *, route=CODEX, turn="t1", session=SESSION):
    provider, model, api_mode = route
    request = {"model": model, "input": [{"role": "user", "content": "do the work"}],
               "reasoning": {"effort": effort_value, "summary": "auto"}}
    out = middleware.on_llm_request(
        request=request, session_id=session, turn_id=turn, provider=provider, model=model,
        api_mode=api_mode)
    return None if out is None else out["request"]["reasoning"]["effort"]


def entry():
    return next(iter(reversed(middleware.session_state().values())))


# ── pure comparison ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("incoming,seen,applied,expected", [
    ("high", "medium", None, True),        # changed, nothing applied yet
    ("high", "medium", "low", True),       # equal to neither
    ("medium", "medium", "low", False),    # unchanged
    ("low", "medium", "low", False),       # echo of the plugin's own value
    ("HIGH ", "high", None, False),        # case / whitespace are not a change
    ("high", None, None, False),           # nothing remembered: no history
    (None, "medium", "low", False),        # no readable incoming value
    ("", "medium", "low", False),
])
def test_is_manual_change(incoming, seen, applied, expected):
    assert effort.is_manual_change(incoming, seen, applied) is expected


# ── behaviour ────────────────────────────────────────────────────────────────

def test_default_is_on():
    assert middleware.DEFAULTS["respect_manual_effort"] is True
    assert middleware._settings()["respect_manual_effort"] is True


def test_stands_down_after_a_manual_change_with_no_scorer_call(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer)
    assert send("low", turn="t1") == "high"           # first request: decided, no detection
    assert send("low", turn="t2") == "high"           # same configured value: still active
    assert len(scorer.calls) == 2
    assert send("medium", turn="t3") is None           # the user picked medium by hand
    assert len(scorer.calls) == 2                      # no scorer call, nothing shared
    assert entry()["manual_effort"] is True and entry()["state"] == "off"
    assert middleware.manual_effort_standing_down(SESSION)
    assert send("medium", turn="t4") is None           # stays stood down
    assert send("low", turn="t5") is None
    assert len(scorer.calls) == 2


def test_first_request_never_counts_as_manual(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer)
    assert send("medium") == "high"
    assert not middleware.manual_effort_standing_down(SESSION)
    assert entry()["manual_effort"] is False


def test_echo_of_the_plugins_own_value_is_not_manual(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer)
    assert send("low", turn="t1") == "high"
    # Desktop selector sync wrote the applied value back into the agent's effort.
    scorer.level = "medium"
    assert send("high", turn="t2") == "medium"
    assert not middleware.manual_effort_standing_down(SESSION)
    assert send("high", turn="t3") == "medium"
    assert len(scorer.calls) == 3
    assert not middleware.manual_effort_standing_down(SESSION)


def test_route_change_is_not_a_manual_change(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer)
    assert send("low", route=CODEX, turn="t1") == "high"
    send("medium", route=OTHER, turn="t2")
    assert not middleware.manual_effort_standing_down(SESSION)
    send("medium", route=OTHER, turn="t3")
    assert not middleware.manual_effort_standing_down(SESSION)
    # Back on the first route with another value: the memory was per current route.
    send("low", route=CODEX, turn="t4")
    assert not middleware.manual_effort_standing_down(SESSION)


def test_setting_false_keeps_todays_behaviour(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer, respect_manual_effort=False)
    assert send("low", turn="t1") == "high"
    assert send("medium", turn="t2") == "high"         # a "manual" change is ignored
    assert send("minimal", turn="t3") == "high"
    assert len(scorer.calls) == 3
    assert not middleware.manual_effort_standing_down(SESSION)


def test_subagents_never_stand_down(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer)
    middleware.on_subagent_start(
        parent_session_id="parent", child_session_id="child-1", child_goal="Rename a symbol.")
    assert send("low", session="child-1", turn="c1") == "high"
    assert send("medium", session="child-1", turn="c2") == "high"
    assert not middleware.manual_effort_standing_down("child-1")
    assert len(scorer.calls) == 2


def test_conversations_are_independent(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer)
    send("low", session="a", turn="t1")
    send("low", session="b", turn="t1")
    assert send("medium", session="a", turn="t2") is None
    assert send("low", session="b", turn="t2") == "high"


def test_off_mode_watches_nothing(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer, mode="off")
    send("low", turn="t1")
    send("medium", turn="t2")
    assert not middleware.manual_effort_standing_down(SESSION)


# ── re-enable ────────────────────────────────────────────────────────────────

def stand_down(monkeypatch, scorer):
    configure(monkeypatch, scorer)
    send("low", turn="t1")
    assert send("medium", turn="t2") is None
    assert middleware.manual_effort_standing_down(SESSION)


@pytest.mark.parametrize("mode", ["auto", "once", "always", "off", "default"])
def test_any_conversation_mode_choice_clears_it(monkeypatch, mode):
    stand_down(monkeypatch, Scorer("high"))
    middleware.set_conversation_mode(SESSION, mode)
    assert not middleware.manual_effort_standing_down(SESSION)


def test_hae_here_verb_clears_it_and_the_plugin_acts_again(monkeypatch):
    scorer = Scorer("high")
    stand_down(monkeypatch, scorer)
    monkeypatch.setattr(history_store, "current_identity", lambda: (SESSION, [SESSION]))
    command.handle("always here")
    assert not middleware.manual_effort_standing_down(SESSION)
    # Re-enabled: the manual value is the new baseline, so it is not flagged again.
    assert send("medium", turn="t3") == "high"
    assert len(scorer.calls) == 2
    assert send("medium", turn="t4") == "high"


def test_dashboard_conversation_scope_clears_it(monkeypatch):
    from test_plugin_api import api

    stand_down(monkeypatch, Scorer("high"))
    api._PINNED = (middleware, command)
    try:
        assert api.set_conversation_mode(SESSION, "auto")["ok"] is True
    finally:
        api._PINNED = None
    assert not middleware.manual_effort_standing_down(SESSION)


def test_a_failed_store_write_does_not_clear_it(monkeypatch):
    stand_down(monkeypatch, Scorer("high"))
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", None)
    with pytest.raises(RuntimeError):
        middleware.set_conversation_mode(SESSION, "auto")
    assert middleware.manual_effort_standing_down(SESSION)


def test_global_mode_command_does_not_clear_it(monkeypatch):
    stand_down(monkeypatch, Scorer("high"))
    command.handle("always")
    assert middleware.manual_effort_standing_down(SESSION)


@pytest.mark.parametrize("hook", ["on_session_reset", "on_session_finalize"])
def test_session_reset_clears_it(monkeypatch, hook):
    stand_down(monkeypatch, Scorer("high"))
    getattr(middleware, hook)(session_id=SESSION)
    assert not middleware.manual_effort_standing_down(SESSION)


# ── status, bounds, privacy ──────────────────────────────────────────────────

def test_status_surfaces_show_it_without_values(monkeypatch):
    stand_down(monkeypatch, Scorer("high"))
    monkeypatch.setattr(history_store, "current_identity", lambda: (SESSION, [SESSION]))
    assert "manual_effort" in command._ENTRY_FIELDS
    payload = json.loads(command.handle("status json"))
    assert payload["settings"]["respect_manual_effort"] is True
    assert payload["last"]["manual_effort"] is True
    assert "standing down" in command.handle("status")
    assert "Respect manual effort: on" in command.handle("status full")


def test_memory_is_bounded_by_max_turns(monkeypatch):
    scorer = Scorer("high")
    configure(monkeypatch, scorer, max_turns=2)
    for index in range(5):
        session = f"s{index}"
        send("low", session=session, turn="t1")
        send("medium", session=session, turn="t2")
    assert len(middleware._STOOD_DOWN) <= 2
    assert len(middleware._EFFORT_WATCH) <= 2


def test_reset_state_drops_both_stores(monkeypatch):
    stand_down(monkeypatch, Scorer("high"))
    middleware.reset_state()
    assert not middleware._STOOD_DOWN and not middleware._EFFORT_WATCH


def test_stood_down_request_is_left_untouched_and_feed_stays_empty(monkeypatch):
    configure(monkeypatch, Scorer("high"))
    send("low", turn="t1")
    before = len(middleware.effort_change_state()["events"])
    assert send("medium", turn="t2") is None
    assert len(middleware.effort_change_state()["events"]) == before
