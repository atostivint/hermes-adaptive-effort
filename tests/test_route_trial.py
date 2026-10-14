"""Dynamic-route trial (issue #58): status codes, cache-counter verdicts, persistence, privacy."""

from __future__ import annotations

import json
import sqlite3

import pytest

from conftest import import_plugin

history_store = import_plugin("history_store")
middleware = import_plugin("middleware")
route_trial = import_plugin("route_trial")
command = import_plugin("command")

SESSION = "TRIAL-SESSION"
ROUTE = ("acme-cloud", "acme/model-x", "chat_completions")
SECRET = "SECRET-PROMPT-TEXT-91c3"
CODEX = ("openai-codex", "gpt-6.1-sol", "codex_responses")
LOW, MEDIUM, HIGH = 0.2, 1.0, 1.9


class ScoreSequence:
    """Scorer that answers the numeric score contract from a fixed sequence."""

    def __init__(self, *scores):
        self.scores, self.calls = list(scores), 0

    def classify_detail(self, prompt):
        self.calls += 1
        return self.scores.pop(0) if self.scores else MEDIUM, None


class Harness:
    def __init__(self, monkeypatch, tmp_path, route=ROUTE, store=True, **settings):
        self.route = route
        self.clock = [1000.0]
        self.turn = 0
        self.prompt = 0
        self.path = tmp_path / "history.sqlite3"
        monkeypatch.setattr(history_store, "_PATH_PROVIDER", (lambda: self.path) if store else None)
        monkeypatch.setattr(route_trial, "_now", lambda: self.clock[0])
        values = {"mode": "auto", **settings}
        monkeypatch.setattr(middleware, "_settings_provider",
                            lambda key, default=None: values.get(key, default))
        self.values = values
        self.scorer = None

    def use(self, monkeypatch, scorer):
        self.scorer = scorer
        monkeypatch.setattr(middleware, "_classifier_factory", lambda **_kw: scorer)

    def send(self, effort="medium", turn=None, session=SESSION):
        if turn is None:
            self.turn += 1
            turn = f"turn-{self.turn}"
        provider, model, api_mode = self.route
        self.request_id = f"req-{turn}-{len(route_trial._PENDING)}-{self.clock[0]}"
        request = {"model": model,
                   "messages": [{"role": "user", "content": f"{SECRET} please"}],
                   "extra_body": {"reasoning": {"enabled": True, "effort": effort}}}
        out = middleware.on_llm_request(
            request=request, session_id=session, provider=provider, model=model,
            api_mode=api_mode, task_id="t", turn_id=turn, api_request_id=self.request_id,
            api_call_count=1)
        return out

    def respond(self, read=0, write=0, grow=500, messages=None):
        self.prompt += grow
        usage = {"input_tokens": 100, "cache_read_tokens": read, "cache_write_tokens": write,
                 "prompt_tokens": self.prompt}
        provider, model, api_mode = self.route
        middleware.on_post_api_request(
            api_request_id=self.request_id, usage=usage, session_id=SESSION,
            provider=provider, model=model, api_mode=api_mode,
            message_count=messages if messages is not None else self.turn * 2 + 1)

    def error(self, status):
        middleware.on_api_request_error(
            api_request_id=self.request_id, status_code=status, session_id=SESSION)

    def status(self):
        return route_trial.status(middleware._route_identity(*self.route))

    def entry(self):
        return [e for k, e in middleware.session_state().items() if e.get("scope") == "turn"][-1]

    def baseline(self):
        """Three unchanged requests: write the cache, read it, then read it again (baseline)."""
        self.send()
        self.respond(write=1000)
        for _ in range(2):
            self.send()
            self.respond(read=1000)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return Harness(monkeypatch, tmp_path)


def effort_of(out):
    return out["request"]["extra_body"]["reasoning"]["effort"] if out else None


# ── trial verdicts ──────────────────────────────────────────────────────────

def test_three_kept_effort_changes_make_the_route_observed(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH, LOW, HIGH))
    harness.baseline()
    assert harness.status()["dynamic_reason"] == "trial"
    assert harness.status()["trial_progress"] == 0

    assert effort_of(harness.send()) == "high"
    harness.respond(read=1000)
    assert harness.status() == {"dynamic": True, "dynamic_reason": "trial", "trial_progress": 1}
    assert effort_of(harness.send()) == "low"
    harness.respond(read=1200)
    assert harness.status()["trial_progress"] == 2
    assert effort_of(harness.send()) == "high"
    harness.respond(read=1400)
    assert harness.status() == {"dynamic": True, "dynamic_reason": "observed",
                                "trial_progress": None}


def test_a_clear_cache_loss_after_one_change_pins_the_route(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH, LOW, HIGH))
    harness.baseline()
    assert effort_of(harness.send()) == "high"
    harness.respond(read=20, write=1500)
    assert harness.status() == {"dynamic": False, "dynamic_reason": "observed_cache_loss",
                                "trial_progress": None}
    # Pinned: the next turn reuses one retained decision instead of scoring again.
    calls = harness.scorer.calls
    harness.send()
    harness.send()
    assert harness.scorer.calls <= calls + 1
    assert harness.entry()["dynamic_reason"] == "observed_cache_loss"
    assert harness.entry()["dynamic"] is False


def test_loss_without_a_working_baseline_is_inconclusive(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, HIGH, LOW))
    harness.send()
    harness.respond(read=1000)
    assert effort_of(harness.send()) == "high"
    harness.respond(read=0, write=1000)
    assert harness.status() == {"dynamic": True, "dynamic_reason": "trial", "trial_progress": 0}


@pytest.mark.parametrize("case", ["ttl", "prompt_rewrite", "message_rewrite", "no_prior_read"])
def test_inconclusive_cases_are_not_counted(harness, monkeypatch, case):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, HIGH) if case == "no_prior_read"
                else ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH))
    if case == "no_prior_read":
        harness.send()
        harness.respond(write=1000)
    else:
        harness.baseline()
    assert effort_of(harness.send()) == "high"
    if case == "ttl":
        harness.clock[0] += route_trial.CACHE_TTL_S + 1
        harness.respond(read=1000)
    elif case == "prompt_rewrite":
        harness.respond(read=1000, grow=-400)
    elif case == "message_rewrite":
        harness.respond(read=1000, messages=1)
    else:
        harness.respond(read=1000)
    assert harness.status()["dynamic_reason"] == "trial"
    assert harness.status()["trial_progress"] == 0


def test_five_responses_without_cache_counters_pin_the_route(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence())
    for _ in range(route_trial.NO_DATA_LIMIT - 1):
        harness.send()
        harness.respond()
        assert harness.status()["dynamic_reason"] == "trial"
    harness.send()
    harness.respond()
    assert harness.status() == {"dynamic": False, "dynamic_reason": "no_cache_data",
                                "trial_progress": None}


def test_a_cache_counter_resets_the_no_data_streak(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence())
    for _ in range(route_trial.NO_DATA_LIMIT - 1):
        harness.send()
        harness.respond()
    harness.send()
    harness.respond(write=800)
    for _ in range(route_trial.NO_DATA_LIMIT - 1):
        harness.send()
        harness.respond()
    assert harness.status()["dynamic_reason"] == "trial"


def test_a_rejected_level_pins_the_route_and_is_never_sent_again(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, HIGH, HIGH, HIGH))
    harness.send()
    harness.respond(write=1000)
    assert effort_of(harness.send()) == "high"
    harness.error(400)
    assert harness.status() == {"dynamic": False, "dynamic_reason": "rejected_level",
                                "trial_progress": None}
    # The retained decision is the rejected level: the request is left alone.
    assert harness.send() is None
    assert harness.send() is None


@pytest.mark.parametrize("code", [401, 408, 429, 500, None])
def test_other_errors_do_not_pin_the_route(harness, monkeypatch, code):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, HIGH))
    harness.send()
    harness.respond(write=1000)
    assert effort_of(harness.send()) == "high"
    harness.error(code)
    assert harness.status()["dynamic_reason"] == "trial"


def test_a_rejection_of_a_request_we_did_not_rewrite_is_ignored(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM))
    assert harness.send() is None  # the scorer agreed with the existing level
    harness.error(400)
    assert harness.status()["dynamic_reason"] == "trial"


# ── persistence and storage failure ─────────────────────────────────────────

def test_verdicts_survive_reset_state(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH, LOW, HIGH))
    harness.baseline()
    for kept in (1000, 1200, 1400):
        harness.send()
        harness.respond(read=kept)
    assert harness.status()["dynamic_reason"] == "observed"
    middleware.reset_state()
    assert route_trial._ROUTES == {}
    assert harness.status()["dynamic_reason"] == "observed"
    ok, stored = history_store.read_route_verdict(route_trial.route_key(
        middleware._route_identity(*ROUTE)))
    assert ok and stored["verdict"] == "observed"


def test_reset_state_clears_in_memory_trial_counters(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH))
    harness.baseline()
    harness.send()
    harness.respond(read=1000)
    assert harness.status()["trial_progress"] == 1
    middleware.reset_state()
    assert harness.status()["trial_progress"] == 0


def test_without_a_store_the_route_stays_pinned_like_before(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, store=False)
    harness.use(monkeypatch, ScoreSequence(HIGH, LOW))
    assert effort_of(harness.send()) == "high"
    assert harness.entry()["dynamic_reason"] == "trial_disabled"
    assert harness.entry()["dynamic"] is False
    assert effort_of(harness.send(turn="turn-next", effort="medium")) == "high"
    assert harness.scorer.calls == 1


def test_a_failing_store_read_keeps_the_route_pinned(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(HIGH, LOW))
    monkeypatch.setattr(history_store, "read_route_verdict", lambda key: (False, None))
    harness.send()
    harness.send()
    assert harness.scorer.calls == 1
    assert harness.entry()["dynamic_reason"] == "trial_disabled"


def test_a_positive_verdict_that_cannot_be_saved_is_not_granted(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH, LOW, HIGH))
    harness.baseline()
    monkeypatch.setattr(history_store, "write_route_verdict", lambda *a, **k: False)
    for kept in (1000, 1200, 1400):
        harness.send()
        harness.respond(read=kept)
    assert harness.status() == {"dynamic": False, "dynamic_reason": "trial_disabled",
                                "trial_progress": None}


def test_a_negative_verdict_that_cannot_be_saved_still_pins(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH))
    harness.baseline()
    monkeypatch.setattr(history_store, "write_route_verdict", lambda *a, **k: False)
    harness.send()
    harness.respond(read=0, write=1500)
    assert harness.status()["dynamic_reason"] == "observed_cache_loss"


def test_the_verdict_table_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_PATH_PROVIDER", lambda: tmp_path / "h.sqlite3")
    for index in range(history_store.MAX_ROUTE_VERDICTS + 5):
        assert history_store.write_route_verdict(f"route-{index}", "observed")
    with sqlite3.connect(tmp_path / "h.sqlite3") as connection:
        count = connection.execute("SELECT COUNT(*) FROM route_verdicts").fetchone()[0]
    assert count == history_store.MAX_ROUTE_VERDICTS


# ── setting, modes and registry ─────────────────────────────────────────────

def test_dynamic_trial_false_keeps_todays_behaviour(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, dynamic_trial=False)
    harness.use(monkeypatch, ScoreSequence(HIGH, LOW, LOW))
    first = harness.send()
    second = harness.send(effort="medium")
    assert effort_of(first) == "high"
    # One retained decision per route: no second scorer call, same level re-applied.
    assert harness.scorer.calls == 1
    assert effort_of(second) == "high"
    assert harness.entry()["dynamic_reason"] == "trial_disabled"
    assert harness.entry()["dynamic"] is False
    assert route_trial._PENDING == {}
    harness.respond(write=1000)
    assert route_trial._SESSIONS == {}


def test_trial_records_no_state_in_once_mode(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, mode="once")
    harness.use(monkeypatch, ScoreSequence(HIGH))
    harness.send()
    harness.respond(write=1000)
    assert route_trial._PENDING == {} and route_trial._SESSIONS == {}
    assert harness.scorer.calls == 1


def test_always_records_verdicts_without_pinning(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, mode="always")
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH, LOW, HIGH, LOW))
    harness.baseline()
    harness.send()
    harness.respond(read=0, write=1500)
    assert harness.status()["dynamic_reason"] == "observed_cache_loss"
    harness.send()
    assert harness.scorer.calls >= 4  # always keeps classifying every turn


def test_a_listed_route_reports_listed_and_makes_no_trial_record(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, route=CODEX)
    harness.use(monkeypatch, type("S", (), {
        "classify_effort_detail": lambda self, prompt, choices: ("medium", None)})())
    request = {"model": CODEX[1], "input": [{"role": "user", "content": "go"}],
               "reasoning": {"effort": "low", "summary": "auto"}}
    middleware.on_llm_request(
        request=request, session_id=SESSION, provider=CODEX[0], model=CODEX[1],
        api_mode=CODEX[2], task_id="t", turn_id="t1", api_request_id="rq", api_call_count=1)
    assert harness.entry()["dynamic_reason"] == "listed"
    assert harness.entry()["dynamic"] is True
    assert route_trial._PENDING == {}


def test_anthropic_and_unknown_api_modes_are_never_trialled(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, route=("anthropic", "claude-unknown", "anthropic_messages"))
    harness.use(monkeypatch, ScoreSequence(HIGH))
    harness.send()
    assert harness.entry()["dynamic_reason"] == "api_mode"
    assert harness.entry()["dynamic"] is False
    other = Harness(monkeypatch, tmp_path, route=("acme-cloud", "m", "weird_mode"))
    other.use(monkeypatch, ScoreSequence(HIGH))
    other.send()
    assert other.entry()["dynamic_reason"] == "api_mode"


def test_a_route_without_an_existing_effort_field_is_not_trialled(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(HIGH))
    middleware.on_llm_request(
        request={"model": ROUTE[1], "messages": [{"role": "user", "content": "x"}]},
        session_id=SESSION, provider=ROUTE[0], model=ROUTE[1], api_mode=ROUTE[2],
        task_id="t", turn_id="t1", api_request_id="rq", api_call_count=1)
    entry = harness.entry()
    assert entry["dynamic"] is None and entry["dynamic_reason"] is None
    assert harness.scorer.calls == 0


# ── status line and JSON ────────────────────────────────────────────────────

@pytest.mark.parametrize("fields, expected", [
    (dict(dynamic=True, dynamic_reason="listed"),
     "Effort changes: every turn — dynamic (listed)"),
    (dict(dynamic=True, dynamic_reason="observed"),
     "Effort changes: every turn — dynamic (observed)"),
    (dict(dynamic=True, dynamic_reason="trial", trial_progress=1),
     "Effort changes: every turn — on trial (1/3)"),
    (dict(dynamic=False, dynamic_reason="observed_cache_loss"),
     "Effort changes: once, then kept — not dynamic (observed cache loss)"),
    (dict(dynamic=False, dynamic_reason="rejected_level"),
     "Effort changes: once, then kept — not dynamic (provider rejected a trial level)"),
    (dict(dynamic=False, dynamic_reason="no_cache_data"),
     "Effort changes: once, then kept — not dynamic (no cache data from the provider)"),
    (dict(dynamic=False, dynamic_reason="api_mode"),
     "Effort changes: once, then kept — not dynamic (cache-hostile API mode)"),
    (dict(dynamic=False, dynamic_reason="trial_disabled"),
     "Effort changes: once, then kept — not dynamic (trial off)"),
    (dict(dynamic=False, dynamic_reason="trial_disabled", mode="once"),
     "Effort changes: once, then kept — not dynamic (trial off); "
     "mode once always keeps one decision"),
    (dict(dynamic=False, dynamic_reason="api_mode", mode="always"),
     "Effort changes: once, then kept — not dynamic (cache-hostile API mode); "
     "mode always changes every turn anyway (prompt-cache risk)"),
])
def test_effort_changes_line_for_every_reason(fields, expected):
    assert command._effort_changes_line({"state": "decided", **fields}) == expected


def test_no_line_before_any_request():
    assert command._effort_changes_line({}) is None


def test_status_text_and_json_expose_the_dynamic_fields(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH))
    harness.baseline()
    harness.send()
    harness.respond(read=1000)
    harness.send()
    assert "dynamic" in command._ENTRY_FIELDS
    assert "dynamic_reason" in command._ENTRY_FIELDS
    payload = json.loads(command.handle("status json"))
    assert payload["settings"]["dynamic_trial"] is True
    last = payload["last"]
    assert last["dynamic"] is True and last["dynamic_reason"] == "trial"
    assert last["trial_progress"] == 1
    assert "Dynamic trial: on" in command.handle("status full")


def test_desktop_event_carries_the_dynamic_fields(harness, monkeypatch):
    events = []
    import hermes_cli.plugin_events as plugin_events
    monkeypatch.setattr(plugin_events, "broadcast_plugin_event",
                        lambda plugin, name, payload: events.append(payload))
    harness.use(monkeypatch, ScoreSequence(HIGH))
    harness.send()
    status = events[-1]["status"]
    assert status["dynamic"] is True and status["dynamic_reason"] == "trial"
    assert events[-1]["schema"].endswith("desktop-status.v2")


# ── privacy and fail-open ──────────────────────────────────────────────────

def test_no_prompt_text_reaches_trial_state_storage_or_status(harness, monkeypatch, caplog):
    harness.use(monkeypatch, ScoreSequence(MEDIUM, MEDIUM, MEDIUM, HIGH))
    caplog.set_level("DEBUG")
    harness.baseline()
    harness.send()
    harness.respond(read=0, write=1500)
    rendered = repr((route_trial._ROUTES, route_trial._SESSIONS, route_trial._PENDING))
    rendered += command.handle("status json") + command.handle("status full")
    rendered += json.dumps(history_store.read_history([SESSION], 10), default=str)
    rendered += "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET not in rendered
    assert SECRET.encode() not in harness.path.read_bytes()
    with sqlite3.connect(harness.path) as connection:
        rows = connection.execute("SELECT * FROM route_verdicts").fetchall()
    assert rows and SECRET not in repr(rows)


@pytest.mark.parametrize("kwargs", [
    {}, {"api_request_id": None}, {"api_request_id": "nope", "usage": None},
    {"api_request_id": "nope", "usage": "garbage", "message_count": "x"},
])
def test_hooks_fail_open_on_garbage(kwargs):
    assert middleware.on_post_api_request(**kwargs) is None
    assert middleware.on_api_request_error(**kwargs, status_code="400") is None


def test_hook_errors_are_swallowed(harness, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(route_trial, "on_response", boom)
    monkeypatch.setattr(route_trial, "on_error", boom)
    assert middleware.on_post_api_request(api_request_id="x", usage={}) is None
    assert middleware.on_api_request_error(api_request_id="x", status_code=400) is None


def test_a_trial_bookkeeping_failure_leaves_the_rewrite_intact(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(HIGH))
    monkeypatch.setattr(route_trial, "note_request",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert effort_of(harness.send()) == "high"


def test_in_memory_trial_state_is_bounded(harness, monkeypatch):
    for index in range(route_trial.MAX_PENDING_REQUESTS + 10):
        route_trial.note_request(f"s{index}", ("p", "m", "chat_completions"),
                                 f"r{index}", "high", False)
    assert len(route_trial._PENDING) == route_trial.MAX_PENDING_REQUESTS
    assert len(route_trial._SESSIONS) == route_trial.MAX_TRACKED_SESSIONS


def test_ending_a_session_drops_its_trial_state(harness, monkeypatch):
    harness.use(monkeypatch, ScoreSequence(MEDIUM))
    harness.send()
    assert route_trial._SESSIONS and route_trial._PENDING
    middleware.on_session_finalize(session_id=SESSION)
    assert route_trial._SESSIONS == {} and route_trial._PENDING == {}
