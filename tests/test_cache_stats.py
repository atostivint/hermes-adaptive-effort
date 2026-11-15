"""Attributable cache loss after *our own* effort change; fakes only, network-free.

The clock and the SQLite path are injected, so every case is deterministic and nothing
touches the operator's profile data.
"""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

cache_stats = import_plugin("cache_stats")
history_store = import_plugin("history_store")
middleware = import_plugin("middleware")
command = import_plugin("command")

ROUTE = ("opencode-go", "deepseek-v4.1-flash", "chat_completions")
OTHER = ("openai-codex", "gpt-6.1-sol", "codex_responses")
DAY = 86_400.0
BASE = 1_800_000_000.0


class Harness:
    def __init__(self, monkeypatch, tmp_path, bind=True):
        self.clock = [BASE]
        self.index = 0
        self.path = tmp_path / "history.sqlite3"
        monkeypatch.setattr(cache_stats, "_now", lambda: self.clock[0])
        monkeypatch.setattr(history_store, "_PATH_PROVIDER",
                            (lambda: self.path) if bind else None)
        cache_stats.reset_state()

    def send(self, effort, session="S", messages=3, cached=2048, prompt=2083, gap=0.0,
             route=ROUTE, usage_extra=None):
        """One request/response pair; returns the bucket ``on_response`` reported."""
        self.clock[0] += gap
        self.index += 1
        request_id = f"r{self.index}"
        cache_stats.note_request(session, route, request_id, effort, messages)
        usage = {"prompt_tokens": prompt}
        if cached is not None:
            usage["cache_read_tokens"] = cached
        usage.update(usage_extra or {})
        return cache_stats.on_response(request_id, usage, messages)

    def row(self, day=None):
        rows = cache_stats.read_rows(7)
        assert rows, "expected at least one row"
        return rows[-1] if day is None else [r for r in rows if r["day"] == day][0]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


# ── counting ────────────────────────────────────────────────────────────────

def test_constant_effort_counts_requests_and_ratio_but_no_change(harness):
    for _ in range(3):
        assert harness.send("low") is None
    assert cache_stats.flush() is True

    row = harness.row()
    assert row["requests"] == 3
    assert row["prompt_tokens"] == 3 * 2083
    assert row["cached_tokens"] == 3 * 2048
    assert row["changes"] == 0
    assert row["cold_after_change"] == 0 and row["kept_after_change"] == 0


def test_a_change_on_a_warm_contiguous_baseline_is_one_cold_miss(harness):
    harness.send("low")
    assert harness.send("high", cached=0, gap=1.0) == "cold_after_change"
    cache_stats.flush()

    row = harness.row()
    assert row["requests"] == 2
    assert row["changes"] == 1
    assert row["cold_after_change"] == 1
    assert row["kept_after_change"] == 0
    assert sum(row[name] for name in cache_stats.EXCLUSIONS) == 0


def test_a_change_that_kept_the_cache_is_counted_as_kept(harness):
    harness.send("low")
    assert harness.send("high", cached=2048, gap=1.0) == "kept_after_change"
    cache_stats.flush()
    row = harness.row()
    assert (row["changes"], row["kept_after_change"], row["cold_after_change"]) == (1, 1, 0)


# ── the exclusions: nothing here may enter the rate ─────────────────────────

def test_a_six_minute_gap_is_an_idle_exclusion(harness):
    harness.send("low")
    assert harness.send("high", cached=0, gap=360.0) == "excluded_idle"
    cache_stats.flush()

    row = harness.row()
    assert row["excluded_idle"] == 1
    assert row["changes"] == 0 and row["cold_after_change"] == 0


def test_a_new_session_is_a_session_exclusion(harness):
    harness.send("low", session="S1")
    assert harness.send("high", cached=0, session="S2", gap=1.0) == "excluded_session"
    cache_stats.flush()
    row = harness.row()
    assert row["excluded_session"] == 1
    assert row["changes"] == 0


def test_a_shrinking_message_count_is_a_compaction_exclusion(harness):
    harness.send("low", messages=8)
    assert harness.send("high", cached=0, messages=3, gap=1.0) == "excluded_shrank"
    cache_stats.flush()
    row = harness.row()
    assert row["excluded_shrank"] == 1
    assert row["changes"] == 0


def test_a_previous_request_without_a_counter_is_excluded(harness):
    harness.send("low", cached=None)
    assert harness.send("high", cached=0, gap=1.0) == "excluded_no_counter"
    cache_stats.flush()
    row = harness.row()
    assert row["excluded_no_counter"] == 1
    assert row["changes"] == 0


def test_a_previous_read_under_256_tokens_is_already_cold(harness):
    harness.send("low", cached=128)
    assert harness.send("high", cached=0, gap=1.0) == "excluded_already_cold"
    cache_stats.flush()
    row = harness.row()
    assert row["excluded_already_cold"] == 1
    assert row["changes"] == 0


def test_a_read_between_ten_and_fifty_percent_lands_between(harness):
    harness.send("low", cached=1000)
    assert harness.send("high", cached=300, gap=1.0) == "between"
    cache_stats.flush()
    row = harness.row()
    assert row["between"] == 1
    assert row["changes"] == 1  # a change was counted, just not classified as cold or kept
    assert row["cold_after_change"] == 0 and row["kept_after_change"] == 0


def test_exclusions_are_checked_in_the_documented_order(harness):
    """Session beats idle: both apply here, one bucket receives it."""
    harness.send("low", session="S1")
    bucket = harness.send("high", cached=0, session="S2", gap=360.0)
    assert bucket == "excluded_session"


def test_a_failed_request_is_dropped_and_counts_nothing(harness):
    harness.send("low")
    cache_stats.note_request("S", ROUTE, "failed", "high", 3)
    cache_stats.on_error("failed")
    assert cache_stats.on_response("failed", {"prompt_tokens": 2083,
                                             "cache_read_tokens": 0}) is None
    cache_stats.flush()
    assert harness.row()["requests"] == 1


# ── persistence ─────────────────────────────────────────────────────────────

def test_days_are_separate_rows_and_carry_no_text(harness):
    harness.send("low")
    cache_stats.flush()
    harness.clock[0] += DAY
    harness.send("low")
    cache_stats.flush()

    rows = cache_stats.read_rows(7)
    assert [row["requests"] for row in rows] == [1, 1]
    # No prompt text, session id or request id can survive in a row.
    blob = json.dumps(rows)
    for forbidden in ("S", "r1", "r2", "prompt text"):
        assert f'"{forbidden}"' not in blob


def test_pruning_keeps_six_days_and_drops_eight(harness):
    harness.clock[0] = BASE - 8 * DAY
    harness.send("low")
    cache_stats.flush()
    eight_days_old = cache_stats._day()
    harness.clock[0] = BASE - 6 * DAY
    harness.send("low")
    cache_stats.flush()
    six_days_old = cache_stats._day()

    assert six_days_old != eight_days_old
    harness.clock[0] = BASE
    harness.send("low")
    cache_stats.flush()

    days = [row["day"] for row in cache_stats.read_rows(7)]
    assert eight_days_old not in days
    assert six_days_old in days


def test_an_unwritable_store_never_raises_and_keeps_the_request_path_clean(
        monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: blocker / "db.sqlite3")
    cache_stats.reset_state()
    harness = object.__new__(Harness)
    harness.clock, harness.index = [BASE], 0
    monkeypatch.setattr(cache_stats, "_now", lambda: harness.clock[0])

    assert harness.send("low") is None
    assert harness.send("high", cached=0, gap=1.0) == "cold_after_change"
    assert cache_stats.flush() is False  # nothing could be written, nothing raised
    assert cache_stats.read_rows(7) == []


# ── /hae cache ──────────────────────────────────────────────────────────────

def test_cache_verb_renders_the_table(harness):
    harness.send("low")
    harness.send("high", cached=0, gap=1.0)

    text = command.handle("cache")

    assert "cache (last 7 days)" in text
    assert "opencode-go/deepseek-v4.1-flash (chat_completions)" in text
    assert "req 2" in text and "cache 49.2%" in text
    assert "cold 1" in text and "excluded 0" in text


def test_cache_verb_json_parses_and_is_empty_on_a_fresh_store(harness):
    payload = json.loads(command.handle("cache json"))
    assert payload["schema"] == command.CACHE_SCHEMA
    assert payload["days"] == 7 and payload["rows"] == []

    harness.send("low")
    harness.send("high", cached=0, gap=1.0)
    payload = json.loads(command.handle("cache json"))
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert (row["provider"], row["model"], row["api_mode"]) == ROUTE
    assert row["requests"] == 2 and row["changes"] == 1 and row["cold_after_change"] == 1
    assert row["cache_ratio"] == pytest.approx(2048 / (2 * 2083), abs=1e-4)


def test_cache_verb_applies_pending_counters_before_rendering(harness):
    harness.send("low")
    # No explicit flush: the verb must not show a stale table.
    assert "req 1" in command.handle("cache")


def test_cache_verb_rejects_an_unknown_argument(harness):
    assert command.handle("cache bogus") == command.USAGE


# ── wiring ──────────────────────────────────────────────────────────────────

def test_the_post_api_request_hook_feeds_the_diagnostic(harness):
    middleware.reset_state()
    cache_stats.reset_state()
    provider, model, api_mode = ROUTE
    middleware._note_cache_request(
        {"session_id": "S", "provider": provider, "model": model, "api_mode": api_mode,
         "api_request_id": "hook-1"},
        {"request": {"model": model, "messages": [{"role": "user", "content": "x"}],
                     "reasoning_effort": "low"}})

    middleware.on_post_api_request(api_request_id="hook-1",
                                   usage={"prompt_tokens": 2083, "cache_read_tokens": 2048},
                                   message_count=1)

    cache_stats.flush()
    row = harness.row()
    assert row["requests"] == 1 and row["cached_tokens"] == 2048
