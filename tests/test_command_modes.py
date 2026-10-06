"""The four public effort-mode verbs and their process-local scope."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

command = import_plugin("command")
middleware = import_plugin("middleware")

MODES = ("auto", "once", "always", "off")
SESSION = "CMD-MODES"


def make_request(effort="medium", text="Design a multi-region failover plan"):
    return {
        "model": "openrouter/x/y",
        "messages": [{"role": "user", "content": text}],
        "extra_body": {"reasoning": {"enabled": True, "effort": effort}},
    }


class CountingJev:
    def __init__(self, score=1.9):
        self.score = score
        self.calls = []

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        return self.score, None


def route(monkeypatch, jev=None, request=None, turn="turn-1"):
    jev = CountingJev() if jev is None else jev
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: jev)
    out = middleware.on_llm_request(
        request=make_request() if request is None else request,
        session_id=SESSION, provider="openrouter", model="openrouter/x/y",
        api_mode="chat_completions", task_id="t", turn_id=turn,
        api_request_id="r1", api_call_count=1,
        middleware_schema_version="hermes.middleware.v1")
    return out, jev


def use_mode(monkeypatch, mode):
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: {"mode": mode}.get(key, default))


@pytest.mark.parametrize("mode", MODES)
def test_mode_verb_is_accepted_and_governs_the_next_request(monkeypatch, mode):
    use_mode(monkeypatch, mode)
    reply = command.handle(mode)
    assert reply != command.USAGE
    assert mode in reply
    assert middleware.mode_override() == mode
    assert (middleware._settings()["mode"], middleware._settings()["mode_source"]) == (
        mode, "override")

    out, jev = route(monkeypatch)
    if mode == "off":
        assert out is None
        assert jev.calls == []
    else:
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "high"
        assert len(jev.calls) == 1


def test_mode_reply_names_its_scope_and_the_persist_path(monkeypatch):
    use_mode(monkeypatch, "off")
    first = command.handle("auto")
    assert "future requests" in first
    assert "not persisted" in first
    assert "plugins.entries.hermes-adaptive-effort.settings.mode" in first
    repeat = command.handle("auto")
    assert "unchanged" in repeat


def test_mode_override_outranks_the_configured_mode(monkeypatch):
    use_mode(monkeypatch, "once")
    command.handle("always")
    settings = middleware._settings()
    assert (settings["mode"], settings["mode_source"]) == ("always", "override")
    payload = json.loads(command.handle("status json"))
    assert (payload["mode"], payload["mode_source"]) == ("always", "override")
    assert payload["settings"]["mode_source"] == "override"
    assert "override" in command.handle("status")


def test_mode_is_process_local_and_nothing_is_persisted(monkeypatch):
    use_mode(monkeypatch, "off")
    command.handle("auto")
    assert middleware._settings()["mode"] == "auto"
    middleware.clear_mode_override()
    assert middleware._settings()["mode"] == "off"


def test_switching_to_off_stops_scoring_and_rewrites_immediately(monkeypatch):
    use_mode(monkeypatch, "always")
    jev = CountingJev(score=0.1)
    first, _ = route(monkeypatch, jev, turn="turn-1")
    assert first["request"]["extra_body"]["reasoning"]["effort"] == "low"
    assert len(jev.calls) == 1

    command.handle("off")
    later, _ = route(monkeypatch, jev, request=make_request(effort="high"), turn="turn-2")
    assert later is None
    assert len(jev.calls) == 1


def test_status_probe_count_does_not_double_count_persistent_decision(monkeypatch):
    use_mode(monkeypatch, "once")
    jev = CountingJev(score=1.9)
    route(monkeypatch, jev, turn="turn-1")
    route(monkeypatch, jev, turn="turn-2")
    payload = json.loads(command.handle("status json"))
    assert payload["counts"]["sessions"] == 1
    assert payload["counts"]["requests"] == 2
    assert payload["counts"]["probes"] == 1
    assert len(jev.calls) == 1


def test_reset_returns_to_the_configured_mode(monkeypatch):
    use_mode(monkeypatch, "once")
    command.handle("always")
    middleware.reset_state()
    assert middleware.mode_override() is None
    assert (middleware._settings()["mode"], middleware._settings()["mode_source"]) == (
        "once", "config")


@pytest.mark.parametrize("unknown", [
    "recommend", "cache_safe", "cache-safe", "inject", "unrecognized",
])
def test_unknown_config_modes_are_disabled(monkeypatch, unknown):
    use_mode(monkeypatch, unknown)
    assert middleware._settings()["mode"] == "off"


def test_unknown_verb_and_stray_arguments_change_nothing():
    before = middleware._settings()["mode"]
    for raw in ("setup", "banana", "auto please", "off extra",
                "recommend", "cache_safe", "inject", "cache-safe"):
        assert command.handle(raw) == command.USAGE, raw
    assert middleware.mode_override() is None
    assert middleware._settings()["mode"] == before


def test_public_mode_list_is_exact_and_help_has_no_legacy_modes():
    assert middleware.VALID_MODES == MODES
    usage = command.USAGE.lower()
    assert "setup" not in usage
    for mode in MODES:
        assert mode in usage
    for legacy in ("recommend", "cache_safe", "cache-safe", "inject"):
        assert legacy not in usage
    assert command.handle("help") == command.USAGE
