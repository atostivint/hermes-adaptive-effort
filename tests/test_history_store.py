"""Prompt-free, bounded per-conversation decision history and its CLI views."""

from __future__ import annotations

import json

from conftest import import_plugin

history_store = import_plugin("history_store")
middleware = import_plugin("middleware")
command = import_plugin("command")


def test_history_is_bounded_and_never_persists_prompt_text(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)

    for index in range(70):
        assert history_store.record_change(
            "conversation-a", ("conversation-a",), f"turn-{index}", "low", "high",
            "provider", f"model-{index}", "chat_completions", "compatible",
            details={"score": 1.9, "prompt": "PRIVATE_HISTORY_PROMPT"}, at=float(index),
        )

    view = history_store.read_history(("conversation-a",), limit=64)
    assert len(view["events"]) == 64
    assert view["events"][0]["model"] == "model-69"
    assert view["events"][-1]["model"] == "model-6"
    assert "PRIVATE_HISTORY_PROMPT" not in path.read_bytes().decode("utf-8", errors="ignore")


def test_current_identity_prefers_the_durable_conversation_id(monkeypatch):
    from gateway import session_context

    values = {"HERMES_SESSION_KEY": "stable-platform-session",
              "HERMES_SESSION_ID": "conversation-after-reset"}
    monkeypatch.setattr(session_context, "get_session_env", lambda name: values.get(name, ""))
    scope, aliases = history_store.current_identity()
    assert scope == "conversation-after-reset"
    assert aliases == ["conversation-after-reset", "stable-platform-session"]


def test_history_isolated_by_conversation_and_reset_clears_only_that_conversation(
        tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER",
                        lambda: tmp_path / "history.sqlite3")
    for conversation, model in (("alpha", "model-alpha"), ("beta", "model-beta")):
        history_store.record_change(
            conversation, (conversation,), f"turn-{conversation}", "absent", "medium",
            "provider", model, "responses", "not_verified", at=10.0,
        )

    alpha = history_store.read_history(("alpha",))
    beta = history_store.read_history(("beta",))
    assert [event["model"] for event in alpha["events"]] == ["model-alpha"]
    assert [event["model"] for event in beta["events"]] == ["model-beta"]

    assert history_store.clear_conversation(("alpha",))
    assert history_store.read_history(("alpha",))["events"] == []
    assert [event["model"] for event in history_store.read_history(("beta",))["events"]] == [
        "model-beta"]


def test_history_deduplicates_the_same_applied_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER",
                        lambda: tmp_path / "history.sqlite3")
    args = ("conversation", ("conversation",), "decision-key", "low", "high",
            "provider", "model", "chat", "sensitive")
    assert history_store.record_change(*args, at=1.0)
    assert history_store.record_change(*args, at=2.0)
    view = history_store.read_history(("conversation",))
    assert len(view["events"]) == 1
    assert view["events"][0]["cache_verdict"] == "sensitive"


def test_profile_journal_retains_at_most_64_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER",
                        lambda: tmp_path / "history.sqlite3")
    for index in range(65):
        assert history_store.record_snapshot(
            f"conversation-{index}", (f"conversation-{index}",),
            {"state": "off", "model": f"model-{index}"}, at=float(index),
        )

    assert history_store.read_history(("conversation-0",))["status"] == {}
    assert history_store.read_history(("conversation-64",))["status"]["model"] == "model-64"


def test_compact_cli_history_and_status_are_scoped_and_readable(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    monkeypatch.setattr(history_store, "current_identity", lambda: ("alpha", ["alpha"]))
    monkeypatch.setattr(
        middleware, "_settings_provider",
        lambda key, default=None: {"mode": "auto"}.get(key, default),
    )
    history_store.record_snapshot("alpha", ("alpha",), {
        "conversation_id": "alpha", "state": "decided", "score": 1.7,
        "label": "high", "target": "high", "mode": "auto",
        "provider": "openai-codex", "model": "gpt-6.1-sol",
        "api_mode": "codex_responses", "cache_verdict": "compatible",
    }, at=5.0)
    history_store.record_change(
        "alpha", ("alpha",), "alpha-turn", "medium", "high", "openai-codex",
        "gpt-6.1-sol", "codex_responses", "compatible",
        details={"score": 1.7, "label": "high", "scorer_provider": "jev",
                 "scorer_model": "jev-latest", "prompt": "PRIVATE_HISTORY_PROMPT"},
        at=6.0,
    )
    history_store.record_change(
        "beta", ("beta",), "beta-turn", "low", "medium", "other-provider",
        "private-other-model", "chat_completions", "sensitive", at=7.0,
    )

    compact_history = command.handle("")
    compact_status = command.handle("status")
    full_history = command.handle("history full")
    full_status = command.handle("status full")
    rendered = "\n".join((compact_history, compact_status, full_history, full_status))

    assert "gpt-6.1-sol" in compact_history
    assert "medium -> high" in compact_history
    assert "Compatible" in compact_history
    assert "private-other-model" not in rendered
    assert "PRIVATE_HISTORY_PROMPT" not in rendered
    assert "Model: gpt-6.1-sol" in compact_status
    assert "Cache: compatible (effort stays out of the prompt cache key)" in compact_status
    assert "API route: codex_responses" in full_history
    assert "Api Mode" in full_status
    assert "history" in command.handle("help").lower()
    assert json.loads(command.handle("status json"))["schema"] == command.STATUS_SCHEMA
    assert "PRIVATE_HISTORY_PROMPT" not in path.read_bytes().decode("utf-8", errors="ignore")


def test_failed_history_storage_does_not_escape_the_fail_open_boundary(tmp_path, monkeypatch):
    directory = tmp_path / "not-a-database-file"
    directory.mkdir()
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: directory)
    event = middleware._record_effort_change(
        "decision", "low", "high", "conversation", "provider", "model", "chat",
        details={"score": 1.5},
    )
    assert event is not None
    assert event["from"] == "low" and event["to"] == "high"
