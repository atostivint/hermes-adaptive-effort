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


def test_route_verdicts_round_trip_and_a_missing_store_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.read_route_verdict("r") == (True, None)  # no file yet: nothing observed
    assert history_store.write_route_verdict("r", "rejected_level", "high")
    assert history_store.read_route_verdict("r") == (
        True, {"verdict": "rejected_level", "detail": "high"})
    assert history_store.write_route_verdict("r", "observed")
    assert history_store.read_route_verdict("r")[1] == {"verdict": "observed", "detail": None}
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", None)
    assert history_store.read_route_verdict("r") == (False, None)
    assert history_store.write_route_verdict("r", "observed") is False
    assert history_store.store_available() is False


# -- hot-path behaviour: schema once, snapshot dedupe, override cache, contention ----------

def _count_connects(monkeypatch):
    calls = []
    real = history_store.sqlite3.connect

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(history_store.sqlite3, "connect", counting)
    return calls


def _count_schema_runs(monkeypatch):
    """Count DDL runs by tracing each connection's statements."""
    runs = []
    real = history_store.sqlite3.connect

    def tracing(*args, **kwargs):
        connection = real(*args, **kwargs)
        connection.set_trace_callback(
            lambda sql: runs.append(sql) if "CREATE TABLE IF NOT EXISTS conversations" in sql
            else None)
        return connection

    monkeypatch.setattr(history_store.sqlite3, "connect", tracing)
    return runs


def test_schema_is_created_once_per_path_and_recreated_after_deletion(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    runs = _count_schema_runs(monkeypatch)
    for index in range(5):
        assert history_store.record_change(
            "a", ("a",), f"k{index}", "low", "high", "p", "m", "chat_completions", "compatible")
    assert len(runs) == 1
    path.unlink()
    assert history_store.record_change(
        "a", ("a",), "again", "low", "high", "p", "m", "chat_completions", "compatible")
    assert len(runs) == 2
    assert [e["to"] for e in history_store.read_history(("a",))["events"]] == ["high"]


def test_missing_tables_are_recreated_on_the_next_write(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.write_mode_override("a", "off")
    connection = sqlite3.connect(str(path))
    try:
        with connection:
            connection.execute("DROP TABLE conversation_modes")
    finally:
        connection.close()
    assert history_store.write_mode_override("a", "always")
    history_store.invalidate_caches()
    assert history_store.read_mode_override(("a",)) == "always"


def test_unchanged_snapshot_is_not_rewritten_but_a_change_is(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    status = {"state": "off", "mode": "off", "model": "m"}
    assert history_store.record_snapshot("s", ("s",), status, at=1.0)
    calls = _count_connects(monkeypatch)
    assert history_store.record_snapshot("s", ("s",), status, at=2.0)
    assert history_store.record_snapshot("s", ("s",), {**status, "updated_at": 3.0}, at=3.0)
    assert calls == []
    assert history_store.record_snapshot("s", ("s",), {**status, "state": "decided"}, at=4.0)
    assert len(calls) == 1
    assert history_store.read_history(("s",))["status"]["state"] == "decided"


def test_snapshot_dedupe_is_dropped_when_the_conversation_is_cleared(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    status = {"state": "off", "model": "m"}
    assert history_store.record_snapshot("s", ("s",), status, at=1.0)
    assert history_store.clear_conversation(("s",))
    assert history_store.read_history(("s",))["status"] == {}
    assert history_store.record_snapshot("s", ("s",), status, at=2.0)
    assert history_store.read_history(("s",))["status"]["model"] == "m"


def test_snapshot_is_rewritten_after_the_refresh_window(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    status = {"state": "off", "model": "m"}
    assert history_store.record_snapshot("s", ("s",), status, at=1.0)
    later = history_store.time.monotonic() + history_store.SNAPSHOT_REFRESH_S + 1
    monkeypatch.setattr(history_store.time, "monotonic", lambda: later)
    calls = _count_connects(monkeypatch)
    assert history_store.record_snapshot("s", ("s",), status, at=2.0)
    assert len(calls) == 1
    assert history_store.read_history(("s",))["status"]["updated_at"] == 2.0


def test_mode_override_reads_are_cached_and_local_writes_invalidate(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.write_mode_override("a", "off")
    assert history_store.read_mode_override(("a",)) == "off"
    calls = _count_connects(monkeypatch)
    for _ in range(5):
        assert history_store.read_mode_override(("a",)) == "off"
    assert calls == []
    # a write is visible at once, with no TTL wait (`/hae <mode> here`)
    assert history_store.write_mode_override("a", "always")
    assert history_store.read_mode_override(("a",)) == "always"
    assert history_store.write_mode_override("a", None)
    assert history_store.read_mode_override(("a",)) is None
    assert history_store.write_mode_override("a", "once")
    assert history_store.read_mode_override(("a",)) == "once"
    assert history_store.clear_conversation(("a",))
    assert history_store.read_mode_override(("a",)) is None


def test_mode_override_cache_expires_so_other_processes_are_seen(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.write_mode_override("a", "off")
    assert history_store.read_mode_override(("a",)) == "off"
    connection = sqlite3.connect(str(path))  # another process writes
    try:
        with connection:
            connection.execute("UPDATE conversation_modes SET mode='always'")
    finally:
        connection.close()
    assert history_store.read_mode_override(("a",)) == "off"
    later = history_store.time.monotonic() + history_store.OVERRIDE_TTL_S + 1
    monkeypatch.setattr(history_store.time, "monotonic", lambda: later)
    assert history_store.read_mode_override(("a",)) == "always"


def test_lock_contention_fails_open_quickly(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.record_change(
        "a", ("a",), "k0", "low", "high", "p", "m", "chat_completions", "compatible")
    blocker = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        started = history_store.time.monotonic()
        assert history_store.record_change(
            "a", ("a",), "k1", "low", "medium", "p", "m", "chat_completions", "compatible",
        ) is False
        assert history_store.record_snapshot("a", ("a",), {"state": "x"}) is False
        assert history_store.read_mode_override(("a",)) is None
        assert history_store.time.monotonic() - started < 0.5
    finally:
        blocker.rollback()
        blocker.close()
    assert history_store.record_change(
        "a", ("a",), "k2", "low", "medium", "p", "m", "chat_completions", "compatible")


def test_contended_override_read_keeps_the_last_known_value(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    assert history_store.write_mode_override("a", "always")
    assert history_store.read_mode_override(("a",)) == "always"
    later = history_store.time.monotonic() + history_store.OVERRIDE_TTL_S + 1
    monkeypatch.setattr(history_store.time, "monotonic", lambda: later)
    blocker = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        assert history_store.read_mode_override(("a",)) == "always"
    finally:
        blocker.rollback()
        blocker.close()


def test_store_lock_is_not_held_during_sqlite_io(tmp_path, monkeypatch):
    import threading
    path = tmp_path / "history.sqlite3"
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: path)
    held = []
    real = history_store.sqlite3.connect

    def probing(*args, **kwargs):
        got = []

        def attempt():
            acquired = history_store._STORE_LOCK.acquire(timeout=1)
            got.append(acquired)
            if acquired:
                history_store._STORE_LOCK.release()

        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join()
        held.append(got[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(history_store.sqlite3, "connect", probing)
    assert history_store.record_change(
        "a", ("a",), "k", "low", "high", "p", "m", "chat_completions", "compatible")
    assert held == [True]


def test_here_override_is_visible_immediately_after_a_cached_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: tmp_path / "history.sqlite3")
    middleware.set_conversation_mode("conv", "always")
    assert middleware.conversation_mode_override(("conv",)) == "always"
    middleware.set_conversation_mode("conv", "off")
    assert middleware.conversation_mode_override(("conv",)) == "off"
    middleware.set_conversation_mode("conv", None)
    assert middleware.conversation_mode_override(("conv",)) is None
    middleware.set_conversation_mode("conv", "once")
    assert middleware.conversation_mode_override(("conv",)) == "once"
