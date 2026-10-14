"""Per-conversation mode override: storage, precedence, isolation, command and API."""

from __future__ import annotations

import json
import sqlite3

import pytest

from conftest import import_plugin

history_store = import_plugin("history_store")
middleware = import_plugin("middleware")
command = import_plugin("command")

MODEL = "openrouter/x/y"


class CountingJev:
    def __init__(self, score=1.9):
        self.score = score
        self.calls = []

    def classify_detail(self, prompt):
        self.calls.append(prompt)
        return self.score, None


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    return path


def use_mode(monkeypatch, mode, **extra):
    settings = {"mode": mode, **extra}
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: settings.get(key, default))


def request():
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Design a multi-region failover plan"}],
        "extra_body": {"reasoning": {"enabled": True, "effort": "medium"}},
    }


def route(monkeypatch, session, jev, turn="turn-1"):
    monkeypatch.setattr(middleware, "_classifier_factory", lambda **kwargs: jev)
    return middleware.on_llm_request(
        request=request(), session_id=session, provider="openrouter", model=MODEL,
        api_mode="chat_completions", task_id="t", turn_id=turn, api_request_id="r1",
        api_call_count=1, middleware_schema_version="hermes.middleware.v1")


def effort(out):
    return out["request"]["extra_body"]["reasoning"]["effort"]


# -- storage -----------------------------------------------------------------

def test_store_round_trip_and_clear(store):
    assert history_store.read_mode_override(("a",)) is None
    assert history_store.write_mode_override("a", "off")
    assert history_store.read_mode_override(("zzz", "a")) == "off"
    assert history_store.write_mode_override("a", "always")
    assert history_store.read_mode_override(("a",)) == "always"
    assert history_store.read_mode_override(("b",)) is None
    assert history_store.write_mode_override("a", None)
    assert history_store.read_mode_override(("a",)) is None


def test_store_resolves_through_aliases(store):
    history_store.record_change("stored", ("stored", "key"), "k", "low", "high", "p", "m",
                                "chat_completions", "compatible")
    history_store.write_mode_override("stored", "once")
    assert history_store.read_mode_override(("key",)) == "once"


def test_store_is_bounded(store):
    for index in range(history_store.MAX_MODE_OVERRIDES + 5):
        assert history_store.write_mode_override(f"c{index}", "auto")
    with sqlite3.connect(str(store)) as connection:
        count = connection.execute("SELECT COUNT(*) FROM conversation_modes").fetchone()[0]
    assert count == history_store.MAX_MODE_OVERRIDES
    assert history_store.read_mode_override(("c0",)) is None
    assert history_store.read_mode_override((f"c{history_store.MAX_MODE_OVERRIDES + 4}",)) == "auto"


def test_store_fails_open_without_path_or_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", None)
    assert history_store.read_mode_override(("a",)) is None
    assert history_store.write_mode_override("a", "off") is False
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not a database" * 50)
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.read_mode_override(("a",)) is None
    assert history_store.write_mode_override("a", "off") is False


def test_store_ignores_invalid_stored_mode_and_rejects_invalid_write(store):
    assert history_store.write_mode_override("a", "turbo") is False
    history_store.write_mode_override("a", "off")
    connection = sqlite3.connect(str(store))
    try:
        with connection:
            connection.execute("UPDATE conversation_modes SET mode='turbo'")
    finally:
        connection.close()
    assert history_store.read_mode_override(("a",)) is None


def test_clear_conversation_drops_its_override_only(store):
    history_store.record_change("a", ("a",), "k", "low", "high", "p", "m",
                                "chat_completions", "compatible")
    history_store.write_mode_override("a", "off")
    history_store.write_mode_override("b", "off")
    assert history_store.clear_conversation(("a",))
    assert history_store.read_mode_override(("a",)) is None
    assert history_store.read_mode_override(("b",)) == "off"


def test_history_clear_works_on_a_file_without_the_modes_table(store):
    history_store.record_change("a", ("a",), "k", "low", "high", "p", "m",
                                "chat_completions", "compatible")
    connection = sqlite3.connect(str(store))
    try:
        with connection:
            connection.execute("DROP TABLE conversation_modes")
    finally:
        connection.close()
    assert history_store.clear_conversation(("a",))
    assert history_store.read_history(("a",))["events"] == []


# -- middleware --------------------------------------------------------------

def test_set_conversation_mode_validates(store):
    assert middleware.set_conversation_mode("a", "OFF") == "off"
    assert middleware.conversation_mode_override(("a",)) == "off"
    assert middleware.set_conversation_mode("a", "default") is None
    assert middleware.conversation_mode_override(("a",)) is None
    with pytest.raises(ValueError, match="unknown mode"):
        middleware.set_conversation_mode("a", "turbo")
    with pytest.raises(ValueError, match="conversation_missing"):
        middleware.set_conversation_mode("  ", "auto")


def test_set_conversation_mode_reports_store_failure(monkeypatch):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", None)
    with pytest.raises(RuntimeError, match="store_unavailable"):
        middleware.set_conversation_mode("a", "auto")


def test_override_off_beats_default_always_without_a_scorer_call(monkeypatch, store):
    use_mode(monkeypatch, "always")
    middleware.set_conversation_mode("A", "off")
    jev = CountingJev()
    assert route(monkeypatch, "A", jev) is None
    assert jev.calls == []


def test_override_always_scores_only_that_conversation(monkeypatch, store):
    use_mode(monkeypatch, "off")
    middleware.set_conversation_mode("A", "always")
    jev = CountingJev()
    assert effort(route(monkeypatch, "A", jev)) == "high"
    assert len(jev.calls) == 1
    other = CountingJev()
    assert route(monkeypatch, "B", other) is None
    assert other.calls == []


def test_override_beats_in_process_mode_and_config(monkeypatch, store):
    use_mode(monkeypatch, "off")
    middleware.set_mode_override("off")
    middleware.set_conversation_mode("A", "once")
    jev = CountingJev()
    assert effort(route(monkeypatch, "A", jev)) == "high"
    assert middleware._settings()["mode"] == "off"
    assert middleware._settings()["mode_source"] == "override"


def test_clearing_the_override_returns_to_the_default(monkeypatch, store):
    use_mode(monkeypatch, "off")
    middleware.set_conversation_mode("A", "always")
    middleware.set_conversation_mode("A", "default")
    jev = CountingJev()
    assert route(monkeypatch, "A", jev) is None
    assert jev.calls == []


def test_changing_the_default_leaves_overrides_alone(monkeypatch, store):
    use_mode(monkeypatch, "off")
    middleware.set_conversation_mode("A", "off")
    middleware.set_mode_override("always")
    assert middleware.conversation_mode_override(("A",)) == "off"
    jev = CountingJev()
    assert route(monkeypatch, "A", jev) is None
    assert jev.calls == []


def test_child_sessions_ignore_the_parent_override(monkeypatch, store):
    use_mode(monkeypatch, "off", subagent_mode="off")
    middleware.set_conversation_mode("child-1", "always")
    middleware.on_subagent_start(parent_session_id="parent-1", child_session_id="child-1",
                                 child_goal="Rename a symbol in 12 files.")
    jev = CountingJev()
    assert route(monkeypatch, "child-1", jev) is None
    assert jev.calls == []


def test_tool_loop_requests_reuse_the_single_decision(monkeypatch, store):
    use_mode(monkeypatch, "off")
    middleware.set_conversation_mode("A", "always")
    jev = CountingJev()
    first = route(monkeypatch, "A", jev, turn="t1")
    second = route(monkeypatch, "A", jev, turn="t1")
    assert effort(first) == effort(second) == "high"
    assert len(jev.calls) == 1


def test_session_reset_clears_the_override(monkeypatch, store):
    use_mode(monkeypatch, "off")
    middleware.set_conversation_mode("A", "always")
    middleware.on_session_reset(old_session_id="A")
    assert middleware.conversation_mode_override(("A",)) is None


def test_storage_error_fails_open_to_the_default(monkeypatch, store):
    use_mode(monkeypatch, "always")

    def boom(identities):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(history_store, "read_mode_override", boom)
    jev = CountingJev()
    assert effort(route(monkeypatch, "A", jev)) == "high"


# -- /hae --------------------------------------------------------------------

def as_conversation(monkeypatch, scope="conv-1"):
    monkeypatch.setattr(history_store, "current_identity", lambda: (scope, [scope]))


def test_here_verb_sets_and_clears_with_the_documented_message(monkeypatch, store):
    use_mode(monkeypatch, "auto")
    as_conversation(monkeypatch)
    reply = command.handle("off here")
    assert reply == ("hermes-adaptive-effort conversation mode: default -> off "
                     "(this conversation only; default mode stays auto)")
    assert middleware.conversation_mode_override(("conv-1",)) == "off"
    assert command.handle("always here").startswith(
        "hermes-adaptive-effort conversation mode: off -> always")
    assert command.handle("default here").startswith(
        "hermes-adaptive-effort conversation mode: always -> default")
    assert middleware.conversation_mode_override(("conv-1",)) is None
    assert middleware.mode_override() is None


def test_here_verb_without_identity_or_store_changes_nothing(monkeypatch, store):
    monkeypatch.setattr(history_store, "current_identity", lambda: (None, []))
    assert "no active conversation" in command.handle("off here")
    as_conversation(monkeypatch)
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", None)
    assert "unavailable" in command.handle("off here")


@pytest.mark.parametrize("raw", ["off there", "default", "off here now", "here",
                                 "default here now"])
def test_stray_args_return_usage(monkeypatch, store, raw):
    as_conversation(monkeypatch)
    assert command.handle(raw) == command.USAGE
    assert middleware.conversation_mode_override(("conv-1",)) is None


def test_usage_lists_the_conversation_verbs():
    assert "/hae auto|once|always|off here" in command.USAGE
    assert "/hae default here" in command.USAGE


def test_status_payload_carries_default_and_conversation_fields(monkeypatch, store):
    use_mode(monkeypatch, "auto")
    as_conversation(monkeypatch)
    payload = json.loads(command.handle("status json"))
    assert payload["mode"] == payload["default_mode"] == "auto"
    assert payload["conversation"] == {"conversation_id": "conv-1", "mode": None,
                                       "effective_mode": "auto", "source": "default"}
    assert "conversation mode: default (auto)" in command.handle("status")
    command.handle("off here")
    payload = json.loads(command.handle("status json"))
    assert payload["mode"] == "auto"
    assert payload["conversation"] == {"conversation_id": "conv-1", "mode": "off",
                                       "effective_mode": "off", "source": "conversation"}
    assert "conversation mode: off (this conversation) · default: auto" in command.handle(
        "status")


def test_status_payload_without_identity_and_with_explicit_id(monkeypatch, store):
    use_mode(monkeypatch, "once")
    monkeypatch.setattr(history_store, "current_identity", lambda: (None, []))
    assert command._status_payload()["conversation"] == {
        "conversation_id": None, "mode": None, "effective_mode": "once", "source": "default"}
    middleware.set_conversation_mode("explicit", "always")
    view = command._status_payload(conversation_id="explicit")["conversation"]
    assert view == {"conversation_id": "explicit", "mode": "always",
                    "effective_mode": "always", "source": "conversation"}


# -- dashboard API -----------------------------------------------------------

def test_dashboard_set_conversation_mode(monkeypatch, store):
    from test_plugin_api import api

    use_mode(monkeypatch, "auto")
    api._PINNED = (middleware, command)
    try:
        out = api.set_conversation_mode("conv-9", "off")
        assert out == {"ok": True, "scope": "conversation", "conversation_id": "conv-9",
                       "before": None, "mode": "off", "default_mode": "auto",
                       "effective_mode": "off"}
        cleared = api.set_conversation_mode("conv-9", "default")
        assert cleared["before"] == "off" and cleared["mode"] is None
        assert cleared["effective_mode"] == "auto"
        assert api.set_conversation_mode("", "off")["error"] == "conversation_missing"
        assert "unknown mode" in api.set_conversation_mode("conv-9", "turbo")["error"]
        assert middleware.mode_override() is None
        monkeypatch.setattr(history_store, "_PATH_PROVIDER", None)
        assert api.set_conversation_mode("conv-9", "off")["error"] == "store_unavailable"
    finally:
        api._PINNED = None


def test_dashboard_status_accepts_a_conversation_id(monkeypatch, store):
    from test_plugin_api import api

    use_mode(monkeypatch, "auto")
    middleware.set_conversation_mode("conv-9", "always")
    api._PINNED = (middleware, command)
    try:
        payload = api.get_status_payload("conv-9")
        assert payload["conversation"]["mode"] == "always"
        assert payload["default_mode"] == "auto"
        assert api.get_status_payload()["conversation"]["mode"] is None
    finally:
        api._PINNED = None
