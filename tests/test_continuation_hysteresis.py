"""Continuation answer and downgrade hysteresis (issue #32); fakes only, network-free."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest

import dialogue_harness as harness
from conftest import import_plugin

middleware = import_plugin("middleware")
command = import_plugin("command")
rubric = import_plugin("rubric")
jev_client = import_plugin("jev_client")
nace_client = import_plugin("nace_client")
openrouter_client = import_plugin("openrouter_client")
openai_client = import_plugin("openai_decision_client")
cloudflare_client = import_plugin("cloudflare_client")
custom_client = import_plugin("custom_client")

GOLDEN = Path(__file__).parent / "fixtures" / "scorer_request_bodies.json"
SESSION = "CONT"
LOW, MEDIUM, HIGH = 0.2, 1.0, 1.8
HISTORY = [
    {"role": "user", "content": "refactor the parser"},
    {"role": "assistant", "content": "plan: split the tokenizer"},
]
CODEX = ("openai-codex", "gpt-6.1-sol", "codex_responses")


@pytest.fixture(autouse=True)
def clean():
    middleware.reset_state()
    yield
    middleware.reset_state()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Scorer:
    """Scripted effort scores and continuation answers; records every call."""

    def __init__(self, efforts, answers=(), clock=None, barrier=None, hold=None, advance=0.0):
        self.efforts = list(efforts)
        self.answers = list(answers)
        self.effort_calls = []
        self.continuation_calls = []
        self.clock, self.barrier, self.hold, self.advance = clock, barrier, hold, advance

    def __call__(self, timeout=None):
        return self

    def _effort(self, prompt):
        self.effort_calls.append(prompt)
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if self.clock is not None:
            self.clock.now += self.advance
        return self.efforts.pop(0)

    def classify_detail(self, prompt):
        return self._effort(prompt), None

    def classify_effort_detail(self, prompt, choices):
        score = self._effort(prompt)
        label = "low" if score < 0.5 else "medium" if score < 1.5 else "high"
        return (label if label in choices else choices[-1]), None

    def classify_continuation(self, prompt):
        self.continuation_calls.append(prompt)
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if self.hold is not None:
            self.hold.wait(timeout=5)
        answer = self.answers.pop(0) if self.answers else 0.0
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple):
            return answer
        return answer, None


def configure(monkeypatch, scorer, **overrides):
    values = {"mode": "always", "dialogue_context_messages": 4,
              "continuation_downgrade_votes": 2}
    values.update(overrides)
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(middleware, "_classifier_factory", scorer)


def body(text, effort="medium"):
    return {"model": "openrouter/x/y", "messages": [*HISTORY, {"role": "user", "content": text}],
            "extra_body": {"reasoning": {"enabled": True, "effort": effort}}}


def turn(n, text="next step", model="openrouter/x/y", session=SESSION):
    request = body(text)
    request["model"] = model
    out = middleware.on_llm_request(
        request=request, session_id=session, turn_id=f"t{n}", provider="openrouter",
        model=model, api_mode="chat_completions")
    applied = (out["request"]["extra_body"]["reasoning"]["effort"] if out else None)
    return applied, middleware.session_state()[f"{session}/t{n}"]


def codex_turn(n, text="next step"):
    request = {"model": "gpt-6.1-sol",
               "input": [{"role": "user", "content": "refactor the parser"},
                         {"role": "assistant", "content": "plan: split the tokenizer"},
                         {"role": "user", "content": text}],
               "reasoning": {"effort": "medium", "summary": "auto"}}
    out = middleware.on_llm_request(
        request=request, session_id=SESSION, turn_id=f"t{n}", provider=CODEX[0],
        model=CODEX[1], api_mode=CODEX[2])
    return (out["request"]["reasoning"]["effort"] if out else None), \
        middleware.session_state()[f"{SESSION}/t{n}"]


# ── hysteresis ───────────────────────────────────────────────────────────────

def test_higher_choice_on_a_continuation_applies_immediately(monkeypatch):
    configure(monkeypatch, Scorer([LOW, HIGH], [1.0, 1.0]))
    assert turn(1)[0] == "low"
    applied, entry = turn(2)
    assert applied == "high" and entry["held"] is False and entry["continuation"] == "yes"


def test_lower_choice_applies_only_after_n_consecutive_lower_votes(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW, LOW], [1.0, 1.0, 1.0]))
    assert turn(1)[0] == "high"
    applied, entry = turn(2)
    assert applied == "high" and entry["held"] is True and entry["label"] == "high"
    assert entry["continuation"] == "yes"
    applied, entry = turn(3)
    assert applied == "low" and entry["held"] is False
    assert middleware._CONTINUITY  # bounded memory holds the applied label only


def test_held_label_is_reclamped_for_the_route(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW], [1.0, 1.0]))
    turn(1)
    _applied, entry = turn(2)
    assert entry["target"] == "high" and entry["label"] == "high"


def test_votes_setting_controls_the_streak_length(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW, LOW, LOW], [1.0] * 4),
              continuation_downgrade_votes=3)
    turn(1)
    assert turn(2)[1]["held"] and turn(3)[1]["held"]
    applied, entry = turn(4)
    assert applied == "low" and not entry["held"]


@pytest.mark.parametrize("resetting", [
    ("equal", [HIGH, LOW, HIGH, LOW], [1.0, 1.0, 1.0, 1.0]),
    ("higher", [MEDIUM, LOW, HIGH, LOW], [1.0, 1.0, 1.0, 1.0]),
    ("no", [HIGH, LOW, LOW], [1.0, 1.0, 0.1]),
    ("unknown", [HIGH, LOW, LOW], [1.0, 1.0, (None, "http_error")]),
])
def test_streak_resets_on_equal_higher_no_or_unknown(monkeypatch, resetting):
    name, efforts, answers = resetting
    configure(monkeypatch, Scorer(efforts, answers))
    turn(1)
    assert turn(2)[1]["held"] is True  # streak 1 of 2
    applied, entry = turn(3)
    if name in ("no", "unknown"):
        # today's behaviour: the new low applies at once, no hold
        assert applied == "low" and entry["held"] is False
    else:
        # equal/higher keeps or raises and resets the streak: one more lower vote is held again
        assert entry["held"] is False
        assert turn(4)[1]["held"] is True


def test_no_continuation_means_todays_behaviour(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW], [0.49]))
    turn(1)
    applied, entry = turn(2)
    assert applied == "low" and entry["held"] is False and entry["continuation"] == "no"


def test_yes_threshold_is_inclusive_half(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW], [0.5, 0.5]))
    turn(1)
    assert turn(2)[1]["continuation"] == "yes"


@pytest.mark.parametrize("answer", [
    (None, "malformed_response"), (None, "timeout"), RuntimeError("boom"), 7.5, "yes", True,
    float("nan")])
def test_missing_malformed_or_failed_continuation_is_ignored(monkeypatch, answer):
    scorer = Scorer([HIGH, LOW], [1.0, answer])
    configure(monkeypatch, scorer)
    turn(1)
    applied, entry = turn(2)
    assert applied == "low" and entry["held"] is False and entry["continuation"] is None
    assert entry["state"] == "decided" and entry["failure"] is None


def test_effort_call_alone_counts_for_breaker_and_latency(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(middleware, "_monotonic", clock)
    scorer = Scorer([HIGH, LOW, LOW], [(None, "http_error")] * 3, clock=clock, advance=0.01)
    configure(monkeypatch, scorer, scorer_pause_failures=1)
    for n in (1, 2, 3):
        turn(n)
    assert len(scorer.continuation_calls) == 3 and len(scorer.effort_calls) == 3
    assert sum(sum(counts) for counts in middleware._SCORER_LATENCY.values()) == 3
    assert middleware.scorer_pause_state(middleware._settings())["paused"] is False


def test_route_change_mid_streak_starts_fresh(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW, LOW, LOW], [1.0] * 4))
    turn(1)
    assert turn(2)[1]["held"] is True  # streak 1 on route A
    applied, entry = turn(3, model="openrouter/other/model")
    assert applied == "low" and entry["held"] is False  # route B has no prior level
    applied, entry = turn(4)  # back to A: its memory was dropped by the route change
    assert applied == "low" and entry["held"] is False


def test_reset_state_and_session_reset_clear_the_memory(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW, LOW], [1.0] * 2))
    turn(1)
    turn(2)
    assert middleware._CONTINUITY
    middleware.on_session_reset(session_id="new", old_session_id=SESSION)
    assert not middleware._CONTINUITY
    configure(monkeypatch, Scorer([HIGH]))
    turn(1, session="other")
    assert middleware._CONTINUITY
    middleware.reset_state()
    assert not middleware._CONTINUITY


def test_memory_is_bounded_by_max_turns(monkeypatch):
    configure(monkeypatch, Scorer([MEDIUM] * 6, [1.0] * 6), max_turns=2)
    for index in range(5):
        turn(1, session=f"s{index}")
        # each session's turn memo is also bounded, so re-seed the scorer script as needed
    assert len(middleware._CONTINUITY) <= 2


# ── scope ────────────────────────────────────────────────────────────────────

def test_auto_on_an_exact_dynamic_route_is_in_scope(monkeypatch):
    scorer = Scorer([HIGH, LOW, LOW], [1.0, 1.0, 1.0])
    configure(monkeypatch, scorer, mode="auto")
    assert codex_turn(1)[0] == "high"
    applied, entry = codex_turn(2)
    assert applied == "high" and entry["held"] is True
    assert codex_turn(3)[0] == "low"
    assert len(scorer.continuation_calls) == 3 and len(scorer.effort_calls) == 3


@pytest.mark.parametrize("mode", ["once", "auto"])
def test_once_and_retained_auto_routes_are_unaffected(monkeypatch, mode):
    scorer = Scorer([HIGH, LOW], [1.0, 1.0])
    configure(monkeypatch, scorer, mode=mode)
    applied, entry = turn(1)
    assert applied == "high" and entry["continuation"] is None and entry["held"] is False
    assert scorer.continuation_calls == [] and not middleware._CONTINUITY


def test_native_choice_route_uses_route_levels(monkeypatch):
    scorer = Scorer([LOW, HIGH, LOW, LOW], [1.0] * 4)
    configure(monkeypatch, scorer)

    def kimi(n):
        request = {"model": "kimi-k3", "reasoning_effort": "max",
                   "messages": [*HISTORY, {"role": "user", "content": "go on"}]}
        out = middleware.on_llm_request(
            request=request, session_id=SESSION, turn_id=f"t{n}", provider="opencode-go",
            model="kimi-k3", api_mode="chat_completions")
        return (out["request"]["reasoning_effort"] if out else None), \
            middleware.session_state()[f"{SESSION}/t{n}"]

    assert kimi(1)[0] == "low"
    # kimi-k3 allows low/high/max: the scorer's "high" for the second turn upgrades at once
    assert kimi(2)[0] == "high"
    assert kimi(3)[1]["held"] is True  # lower, vote 1
    applied, entry = kimi(4)
    assert applied == "low" and entry["held"] is False


# ── off, or no dialogue context: nothing changes ─────────────────────────────

def test_off_and_inactive_reproduce_master_bodies_and_make_no_second_call(monkeypatch):
    golden = json.loads(GOLDEN.read_text())
    for extra in ({}, {"continuation_downgrade_votes": 0},
                  {"continuation_downgrade_votes": 3},
                  {"continuation_downgrade_votes": 3, "dialogue_context_messages": 0}):
        for adapter in harness.ADAPTERS:
            for route in harness.ROUTES:
                got = harness.run(monkeypatch, adapter, route, extra_settings=extra).requests
                assert json.loads(json.dumps(got)) == golden[f"{adapter}/{route}"], (
                    extra, adapter, route)


def test_enabled_sends_unchanged_effort_body_plus_a_separate_noul_call(monkeypatch):
    dialogue = {"dialogue_context_messages": 6}
    for adapter in harness.ADAPTERS:
        for route in harness.ROUTES:
            base = harness.run(monkeypatch, adapter, route, extra_settings=dialogue).requests
            both = harness.run(monkeypatch, adapter, route, extra_settings={
                **dialogue, "continuation_downgrade_votes": 2}).requests
            assert len(base) == 1 and len(both) == 2, (adapter, route)
            assert base[0] in both, (adapter, route)  # the effort request is byte-identical
            other = next(item for item in both if item != base[0])
            wire = json.loads(other["body"])
            text = json.dumps(wire)
            assert "noul" in text or "predicate" in text
            assert "effort" not in json.dumps(wire.get("questions"))
            prompt = wire["state"]["prompt"] if "state" in wire else wire["input"]
            assert prompt == (json.loads(base[0]["body"])["state"]["prompt"]
                              if "state" in wire else json.loads(base[0]["body"])["input"])


def test_status_reports_inactive_without_dialogue_context(monkeypatch):
    configure(monkeypatch, None, dialogue_context_messages=0, continuation_downgrade_votes=2)
    assert "continuation: inactive (no dialogue context)" in \
        command.handle("status full").lower()
    configure(monkeypatch, None, continuation_downgrade_votes=0)
    full = command.handle("status full").lower()
    assert "continuation" not in full.replace("continuation_", "").replace(
        "goal continuation escalation: off", "")
    configure(monkeypatch, None, continuation_downgrade_votes=2)
    assert "Continuation: active" in command.handle("status full")


@pytest.mark.parametrize("raw,expected", [
    (None, 0), (0, 0), ("2", 2), (5, 5), (99, 5), (-1, 0), ("junk", 0), (True, 0), (2.9, 2)])
def test_votes_setting_is_clamped(raw, expected):
    assert middleware._downgrade_votes(raw) == expected


def test_entry_fields_and_desktop_event_carry_continuation_and_held(monkeypatch):
    assert {"continuation", "held"} <= set(command._ENTRY_FIELDS)
    configure(monkeypatch, Scorer([HIGH, LOW], [1.0, 1.0]))
    turn(1)
    _applied, entry = turn(2)
    public = command._public_entry("s", entry)
    assert public["continuation"] == "yes" and public["held"] is True


def test_applied_change_feed_is_unchanged(monkeypatch):
    configure(monkeypatch, Scorer([HIGH, LOW, LOW], [1.0, 1.0]))
    turn(1), turn(2), turn(3)
    state = middleware.effort_change_state()
    for event in state["events"]:
        assert set(event) == {"id", "from", "to", "at"}


# ── parallel calls and the shared deadline ───────────────────────────────────

def test_effort_and_continuation_calls_run_in_parallel(monkeypatch):
    barrier = threading.Barrier(2)  # each call waits for the other: serial calls would time out
    scorer = Scorer([HIGH, LOW], [1.0], barrier=barrier)
    configure(monkeypatch, scorer)
    turn(1)  # first turn: no dialogue? it has dialogue, so both calls meet at the barrier
    assert len(scorer.effort_calls) == 1 and len(scorer.continuation_calls) == 1


def test_continuation_call_shares_the_effort_deadline(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(middleware, "_monotonic", clock)
    hold = threading.Event()
    scorer = Scorer([HIGH, LOW], [1.0, 1.0], clock=clock, hold=hold, advance=3.5)
    configure(monkeypatch, scorer, timeout_s=3.0)
    try:
        applied, entry = turn(1)
        # effort used the whole 3 s budget: the still-running continuation call is ignored
        assert applied == "high" and entry["continuation"] is None and entry["held"] is False
    finally:
        hold.set()


def test_continuation_answer_within_deadline_is_used(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(middleware, "_monotonic", clock)
    scorer = Scorer([HIGH, LOW], [1.0, 1.0], clock=clock, advance=1.0)
    configure(monkeypatch, scorer, timeout_s=3.0)
    turn(1)
    assert turn(2)[1]["continuation"] == "yes"


def test_failed_effort_call_ignores_continuation(monkeypatch):
    class Failing(Scorer):
        def classify_detail(self, prompt):
            self.effort_calls.append(prompt)
            return None, "timeout"

        classify_effort_detail = None

    configure(monkeypatch, Failing([]), )
    applied, entry = turn(1)
    assert applied is None and entry["state"] == "failed" and entry["continuation"] is None


def test_tool_loop_reuses_the_held_decision_without_new_calls(monkeypatch):
    scorer = Scorer([HIGH, LOW], [1.0, 1.0])
    configure(monkeypatch, scorer)
    turn(1)
    for _ in range(3):  # three API requests of the same user turn
        request = body("next step")
        middleware.on_llm_request(
            request=request, session_id=SESSION, turn_id="t2", provider="openrouter",
            model="openrouter/x/y", api_mode="chat_completions")
    assert len(scorer.effort_calls) == 2 and len(scorer.continuation_calls) == 2
    assert middleware.session_state()[f"{SESSION}/t2"]["held"] is True


# ── per-adapter noul request and answer shape ────────────────────────────────

class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def capture(client_factory, payload):
    seen = {}

    def transport(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return Response(json.dumps(payload).encode("utf-8"))

    client = client_factory(transport)
    return seen, client.classify_continuation("TASK TEXT")


NOUL = {"answers": {"continuation": {"type": "noul", "noul": 0.83}}}
CF = {"success": True, "errors": [], "result": NOUL}
PREDICATE = {"answers": [{"type": "predicate", "name": "continuation", "probability": 0.83}]}

SYSTEMONE_CLIENTS = {
    "jev": lambda t: jev_client.JevClient(api_key="k", transport=t),
    "nace": lambda t: nace_client.NaceClient(api_key="k", transport=t),
    "openrouter": lambda t: openrouter_client.OpenRouterClient(
        model="vendor/scorer", api_key="k", transport=t),
    "custom": lambda t: custom_client.CustomClient(
        endpoint="https://scorer.example/v1/score", model="m", transport=t),
}


@pytest.mark.parametrize("name", sorted(SYSTEMONE_CLIENTS))
def test_system_one_family_sends_one_noul_question(name):
    seen, (value, failure) = capture(SYSTEMONE_CLIENTS[name], NOUL)
    assert (value, failure) == (0.83, None)
    questions = seen["body"]["questions"]
    assert list(questions) == ["continuation"] and questions["continuation"]["type"] == "noul"
    assert questions["continuation"]["instructions"] == rubric.CONTINUATION_INSTRUCTIONS
    assert seen["body"]["state"]["prompt"] == "TASK TEXT"


def test_openrouter_keeps_its_privacy_routing_on_the_continuation_call():
    seen, _ = capture(SYSTEMONE_CLIENTS["openrouter"], NOUL)
    assert seen["body"]["provider"] == {"zdr": True, "data_collection": "deny"}


def test_cloudflare_clef_sends_a_noul_question_and_reads_the_rest_envelope():
    factory = lambda t: cloudflare_client.CloudflareClient(  # noqa: E731
        account_id=harness.ACCOUNT_ID, api_key="k", transport=t)
    seen, (value, failure) = capture(factory, CF)
    assert (value, failure) == (0.83, None)
    assert seen["body"]["questions"]["continuation"]["type"] == "noul"
    for bad in ({"success": False, "errors": [], "result": NOUL},
                {"success": True, "errors": [{"code": 1}], "result": NOUL}, NOUL):
        assert capture(factory, bad)[1] == (None, "malformed_response")


def test_openai_decisions_sends_one_boolean_predicate_question():
    factory = lambda t: openai_client.OpenAIDecisionClient(api_key="k", transport=t)  # noqa: E731
    seen, (value, failure) = capture(factory, PREDICATE)
    assert (value, failure) == (0.83, None)
    assert seen["body"]["questions"] == [rubric.continuation_decisions_question()]
    assert seen["body"]["questions"][0]["type"] == "predicate"
    refusal = {"answers": [{"type": "refusal", "name": "continuation"}]}
    assert capture(factory, refusal)[1] == (None, "malformed_response")


@pytest.mark.parametrize("payload", [
    {}, {"answers": {}}, {"answers": {"continuation": {"noul": 1.5}}},
    {"answers": {"continuation": {"noul": True}}}, {"answers": {"continuation": {"noul": "0.9"}}},
    {"answers": {"continuation": {"type": "score", "noul": 0.9}}},
    {"answers": {"continuation": {"noul": float("nan")}}}, {"answers": {"effort": {"noul": 0.9}}}])
def test_malformed_system_one_answers_are_rejected(payload):
    assert capture(SYSTEMONE_CLIENTS["jev"], payload)[1] == (None, "malformed_response")


def test_bare_probability_answer_is_accepted():
    assert rubric.systemone_noul({"answers": {"continuation": 0.25}}) == (0.25, None)


def test_effort_questions_never_contain_the_continuation_question():
    seen = {}

    def transport(request, timeout):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return Response(b"{}")

    jev_client.JevClient(api_key="k", transport=transport).classify_detail("task")
    assert list(seen["body"]["questions"]) == ["effort"]


def test_continuation_noul_question_carries_documented_true_false_criteria():
    question = rubric.continuation_questions()["continuation"]
    assert question["type"] == "noul"
    assert set(question["criteria"]) == {"true", "false"}
    assert all(isinstance(text, str) and text for text in question["criteria"].values())
