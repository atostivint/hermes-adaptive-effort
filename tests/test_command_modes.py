"""/hermes-adaptive-effort off|recommend|auto|cache_safe — the mode verbs (acceptance criterion 2).

Two defects are pinned here. First, ``_dispatch`` answered the usage banner for every
verb except ``help``/``status``/``probe``, so none of the four documented modes could
be set from a chat at all. Second, that banner pointed at ``/hermes-adaptive-effort setup``, which
does not exist anywhere: the host API (``hermes_cli/plugins.py``) exposes
``register_command`` and nothing else, so there is no "setup" wizard to delegate to.

The scope of the fix is deliberate: a mode set from the command applies to FUTURE
requests of THIS process, and is never written to the operator's ``config.yaml``
(the reply says both). ``plugins.entries.hermes-adaptive-effort.settings.mode`` is the documented
way to make a choice stick across restarts.
"""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

command = import_plugin("command")
middleware = import_plugin("middleware")

MODES = ("off", "recommend", "auto", "cache_safe")
SESSION = "CMD-MODES"


def test_inject_mode_command_is_explicit_and_override_only(monkeypatch):
    before = middleware._live_config()
    assert command.handle("inject") != command.USAGE
    assert middleware.mode_override() == "inject"
    assert middleware._live_config() == before
    assert "inject" in command.USAGE
    assert command.handle("inject extra") == command.USAGE
    assert middleware.mode_override() == "inject"


def make_request(effort="medium", text="Design a multi-region failover plan"):
    """Recorded OpenAI-compatible shape: reasoning already on, effort pinned."""
    return {
        "model": "openrouter/x/y",
        "messages": [{"role": "user", "content": text}],
        "extra_body": {"reasoning": {"enabled": True, "effort": effort}},
    }


class CountingJev:
    """Fake transport: one deterministic score, and a count of the probes made."""

    def __init__(self, score=1.9):
        self.score = score
        self.calls = []

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        return self.score, None


def route(monkeypatch, jev=None, request=None, api_mode="chat_completions",
          provider="openrouter", model="openrouter/x/y"):
    """One request through the plugin callback, on a counting transport."""
    jev = CountingJev() if jev is None else jev
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: jev)
    out = middleware.on_llm_request(
        request=make_request() if request is None else request,
        session_id=SESSION, provider=provider, model=model, api_mode=api_mode,
        task_id="t", turn_id="turn-1", api_request_id="r1", api_call_count=1,
        middleware_schema_version="hermes.middleware.v1")
    return out, jev


def use_mode(monkeypatch, mode):
    """Config seam: the FILE says *mode*; a runtime override still outranks it."""
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: {
            "mode": mode}.get(key, default))


# ── the verbs do what the banner says ───────────────────────────────────────

@pytest.mark.parametrize("mode", MODES)
def test_mode_verb_is_accepted_and_governs_the_next_request(monkeypatch, mode):
    use_mode(monkeypatch, mode)
    reply = command.handle(mode)
    assert reply != command.USAGE, f"{mode} was answered with the banner"
    assert mode in reply
    assert middleware.mode_override() == mode
    settings = middleware._settings()
    assert (settings["mode"], settings["mode_source"]) == (mode, "override")

    out, jev = route(monkeypatch)
    if mode == "off":
        assert out is None                      # nothing rewritten...
        assert jev.calls == []                  # ...and no scorer call spent
    elif mode == "recommend":
        assert out is not None                  # classified and reported...
        assert out["request"]["extra_body"]["reasoning"]["effort"] == "medium"
        assert len(jev.calls) == 1              # ...but never applied
    else:                                       # auto, cache_safe on a safe route
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
    assert "not persisted" in repeat


def test_mode_override_outranks_the_configured_mode(monkeypatch):
    use_mode(monkeypatch, "recommend")
    assert (middleware._settings()["mode"], middleware._settings()["mode_source"]) == (
        "recommend", "config")

    command.handle("auto")

    settings = middleware._settings()
    assert (settings["mode"], settings["mode_source"]) == ("auto", "override")
    payload = json.loads(command.handle("status json"))
    assert (payload["mode"], payload["mode_source"]) == ("auto", "override")
    assert payload["settings"]["mode_source"] == "override"
    assert "override" in command.handle("status")   # the text line says where it came from


def test_mode_is_process_local_and_nothing_is_persisted(monkeypatch):
    """A restart must fall back to the configured mode: the command writes no file."""
    use_mode(monkeypatch, "off")
    command.handle("auto")
    assert middleware._settings()["mode"] == "auto"     # in force for this process
    middleware.clear_mode_override()                     # what a restart does
    assert middleware._settings()["mode"] == "off"       # the config seam never moved


def test_reset_returns_to_the_configured_mode(monkeypatch):
    use_mode(monkeypatch, "recommend")
    command.handle("cache_safe")
    assert middleware._settings()["mode"] == "cache_safe"

    middleware.reset_state()

    assert middleware.mode_override() is None
    settings = middleware._settings()
    assert (settings["mode"], settings["mode_source"]) == ("recommend", "config")


def test_unknown_verb_and_stray_arguments_change_nothing():
    before = middleware._settings()["mode"]
    for raw in ("setup", "banana", "auto please", "off extra", "cache_safe --now"):
        assert command.handle(raw) == command.USAGE, raw
    assert middleware.mode_override() is None
    assert middleware._settings()["mode"] == before


def test_usage_advertises_every_mode_and_no_dead_verb():
    usage = command.USAGE.lower()
    assert "setup" not in usage
    for mode in MODES:
        assert mode in usage
    assert command.handle("help") == command.USAGE
