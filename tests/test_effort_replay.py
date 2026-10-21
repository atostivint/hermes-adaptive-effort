"""Effort replay: corpus, checks, cheapest-passing logic, runner and writer (network-free)."""

from __future__ import annotations

import datetime
import importlib.util
import io
import json
import re
import sys
import urllib.error
from decimal import Decimal

import pytest

from conftest import PLUGIN_DIR, import_plugin

effort = import_plugin("effort")
SCRIPTS = PLUGIN_DIR / "scripts"
CORPUS = PLUGIN_DIR / "tests" / "fixtures" / "effort_replay_corpus.json"
KEY_LIKE = re.compile(r"(sk-[A-Za-z0-9]{8,}|Bearer\s+\S+|api[_-]?key|authorization)", re.I)
LEVELS = ("low", "medium", "high", "max")
NOW = datetime.datetime(2026, 10, 21, 9, 0, tzinfo=datetime.timezone.utc)


def _load(name):
    module_name = f"{name}_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(module_name, str(SCRIPTS / f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


checks = _load("replay_checks")
labels = _load("replay_labels")
replay = _load("run_effort_replay")
scorer_eval = _load("run_scorer_eval")
corpus = json.loads(CORPUS.read_text(encoding="utf-8"))


# ── corpus ───────────────────────────────────────────────────────────────────

def test_corpus_shape():
    items = corpus["items"]
    assert corpus["schema"] == "hermes-adaptive-effort.replay-corpus.v1" and corpus["version"]
    assert 25 <= len(items) <= 40
    assert len({item["id"] for item in items}) == len(items)
    assert replay.validate_replay_corpus(corpus, effort.CHOICE_LEVELS) == []
    assert {item["check"]["type"] for item in items} == set(checks.CHECK_TYPES)
    assert {item["expected"] for item in items} <= set(LEVELS)


def test_corpus_has_no_code_execution_checks_or_key_like_text():
    text = CORPUS.read_text(encoding="utf-8")
    assert not KEY_LIKE.search(text)
    assert all(item["check"]["type"] in checks.CHECK_TYPES for item in corpus["items"])


def test_validation_reports_bad_checks_and_schema():
    bad = {"schema": "x", "version": "v", "items": [
        {"id": "a", "prompt": "p", "expected": "low", "tags": ["ambiguous"],
         "check": {"type": "shell", "cmd": "true"}}]}
    problems = replay.validate_replay_corpus(bad, effort.CHOICE_LEVELS)
    assert "corpus schema mismatch" in problems and any("check type" in p for p in problems)


# ── checks ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("check,good,bad", [
    ({"type": "exact", "answer": "Tenalp"}, ["tenalp", " `Tenalp`. ", '"tenalp"'],
     ["tenalps", "the answer is tenalp", ""]),
    ({"type": "regex", "pattern": r"^\W*len\W*$"}, ["len", "`len`"],
     ["length", "size"]),
    ({"type": "numeric", "answer": 120, "tolerance": 0.5}, ["so 2h = 120\n120", "120.4"],
     ["121", "no number", ""]),
    ({"type": "numeric", "answer": 1200}, ["1,200", "total: 1200"], ["1,201"]),
    ({"type": "json_schema", "schema": {"type": "object", "required": ["a"], "properties": {
        "a": {"const": 3}, "b": {"enum": ["x", "y"]}}, "additionalProperties": False}},
     ['{"a": 3}', '```json\n{"a": 3, "b": "x"}\n```', 'Here: {"a": 3} done'],
     ['{"a": 4}', '{"a": 3, "c": 1}', '{"b": "x"}', 'not json', '{"a": true}', '[3]']),
])
def test_check_accepts_and_rejects(check, good, bad):
    assert checks.validate_check(check) == []
    assert all(checks.run_check(check, text) for text in good)
    assert not any(checks.run_check(check, text) for text in bad)


def test_json_schema_subset_and_unsupported_keywords():
    schema = {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}}
    check = {"type": "json_schema", "schema": schema}
    assert checks.run_check(check, "[1, 5]") and not checks.run_check(check, "[0]")
    assert not checks.run_check(check, "[1.5]") and not checks.run_check(check, "[true]")
    assert checks.validate_check({"type": "json_schema", "schema": {"pattern": "x"}}) \
        == ["$: unsupported keyword pattern"]


def test_check_definitions_are_validated_and_never_execute():
    assert checks.validate_check({"type": "regex", "pattern": "("}) == ["regex pattern"]
    assert checks.validate_check({"type": "numeric", "answer": "1"})
    assert checks.validate_check({"type": "exact"}) and checks.validate_check(None)
    # a model answer is data: an expression is never evaluated
    assert not checks.run_check({"type": "numeric", "answer": 4}, "__import__('os')")
    assert not checks.run_check({"type": "exact", "answer": "a"}, None)


REFERENCE = {
    "r019": "return c", "r020": "len", "r021": r"^\d{5}$", "r022": "DISTINCT", "r023": "<",
    "r024": "201",
    "r025": '{"order_id":4821,"customer":"Mira Vance","items":3,"total":59.9,"currency":"EUR"}',
    "r026": '{"primes":[23,29,31,37,41,43,47]}', "r027": '{"count":5,"longest":"quick"}',
    "r028": '{"total":83,"fullest_shelf":4,"average":20.75}',
    "r029": '{"project_days":6,"critical_path":["A","B","D"]}',
    "r030": '{"Ana":"fish","Ben":"cat","Cleo":"dog"}',
}


def test_every_corpus_item_passes_with_its_reference_answer():
    for item in corpus["items"]:
        answer = REFERENCE.get(item["id"], str(item["check"].get("answer")))
        assert checks.run_check(item["check"], answer), item["id"]


def test_every_corpus_check_rejects_an_empty_answer():
    assert not any(checks.run_check(item["check"], "") for item in corpus["items"])


# ── cheapest-passing logic ───────────────────────────────────────────────────

P, F, R = labels.PASS, labels.FAIL, labels.REJECTED


def test_cheapest_passing_level_needs_all_samples_by_default():
    derived = labels.derive_label(LEVELS, {"low": [P, F, P], "medium": [P, P, P],
                                           "high": [P, P, P], "max": [P, P, P]}, 3)
    assert derived["label"] == "medium" and not derived["dropped"] and not derived["floor"]
    assert derived["pass_rate"]["low"] == pytest.approx(0.6667, abs=1e-4)
    assert not derived["non_monotonic"]


def test_threshold_lowers_the_bar():
    outcomes = {"low": [P, F, P], "medium": [P, P, P], "high": [P, P, P], "max": [P, P, P]}
    assert labels.derive_label(LEVELS, outcomes, 3, 0.6)["label"] == "low"


def test_all_levels_failing_drops_the_item():
    derived = labels.derive_label(LEVELS, {level: [F, F, F] for level in LEVELS}, 3)
    assert derived["label"] is None and derived["dropped"] and not derived["floor"]
    assert labels.derive_label(LEVELS, {level: [F, F, F] for level in LEVELS}, 3, 0.1)["dropped"]


def test_non_monotonic_and_floor():
    outcomes = {"low": [P, P, P], "medium": [P, F, P], "high": [P, P, P], "max": [P, P, P]}
    derived = labels.derive_label(LEVELS, outcomes, 3)
    assert derived["label"] == "low" and derived["floor"] and derived["non_monotonic"]


def test_rejected_level_is_not_a_failure_and_is_skipped():
    outcomes = {"low": [F, F, F], "medium": [R, R, R], "high": [P, P, P], "max": [P, P, P]}
    derived = labels.derive_label(LEVELS, outcomes, 3)
    assert derived["label"] == "high" and derived["pass_rate"]["medium"] is None
    assert derived["rejected_levels"] == ["medium"] and not derived["non_monotonic"]
    # the lowest ACCEPTED level is the floor
    only = {"low": [R, R, R], "medium": [P, P, P], "high": [P, P, P], "max": [P, P, P]}
    assert labels.derive_label(LEVELS, only, 3)["floor"]


# ── runner ───────────────────────────────────────────────────────────────────

ROUTE = {"provider": "opencode-go", "model": "m-test", "api_mode": "chat_completions"}
ANSWER = {item["prompt"]: item for item in corpus["items"]}


def _good_text(item):
    check = item["check"]
    if check["type"] == "numeric":
        return str(check["answer"])
    if check["type"] == "exact":
        return check["answer"]
    return None


class Fake:
    """Fake target: ``rule(item, level)`` -> (text, usage) or raises."""

    def __init__(self, rule):
        self.rule, self.calls = rule, []

    def __call__(self, prompt, level):
        self.calls.append((prompt, level))
        return self.rule(ANSWER[prompt], level)


def _passing(item, level):
    text = _good_text(item)
    return (text if text is not None else "wrong"), {
        "prompt_tokens": 50, "completion_tokens": {"low": 10, "medium": 20, "high": 40,
                                                   "max": 80}[level],
        "completion_tokens_details": {"reasoning_tokens": 5}}


def _run(target, **kw):
    kw.setdefault("limit", 4)
    return replay.run_replay(corpus, "e" * 64, ROUTE, LEVELS, 3, target, now=NOW, **kw)


def test_call_count_is_items_levels_samples():
    fake = Fake(_passing)
    result = _run(fake, limit=4)
    assert len(fake.calls) == result["calls"] == 4 * 4 * 3
    assert result["items"] == 4 and result["aborted"] is None


def test_labels_tokens_and_reasoning_come_from_usage():
    result = _run(Fake(_passing), limit=2)
    row = result["results"][0]
    assert row["tokens"] == {"low": 10.0, "medium": 20.0, "high": 40.0, "max": 80.0}
    assert row["reasoning_tokens"]["low"] == 5.0
    labelled = [r for r in result["results"] if r["label"]]
    assert all(r["label"] == "low" and r["floor"] for r in labelled)
    assert result["completion_tokens_total"]["low"] == 10 * 3 * 2


def test_max_calls_aborts_and_drops_the_incomplete_item():
    fake = Fake(_passing)
    result = _run(fake, max_calls=30, limit=10)
    assert len(fake.calls) == 30 and result["aborted"] == "max_calls"
    assert result["items"] == 30 // 12  # only complete items are kept


def test_max_cost_aborts():
    result = _run(Fake(_passing), limit=10, max_cost=Decimal("0.0001"),
                  prices=(Decimal("1000"), Decimal("1000")))
    assert result["aborted"] == "max_cost" and result["calls"] < 10 * 12
    assert Decimal(result["estimated_cost_usd"]) >= Decimal("0.0001")


def test_missing_usage_aborts_cost_guarded_run():
    result = _run(Fake(lambda item, level: ("x", {})), max_cost=Decimal("1"),
                  prices=(Decimal(1), Decimal(1)))
    assert result["aborted"] == "usage_missing" and result["calls"] == 1


def test_rejected_level_is_classified_not_failed_and_stops_billing_it():
    def rule(item, level):
        if level == "medium":
            raise replay.TargetRejected(400, "invalid_reasoning_effort")
        return _passing(item, level)
    fake = Fake(rule)
    result = _run(fake, limit=3)
    state = result["level_status"]["medium"]
    assert state["accepted"] == 0 and state["rejected"] == replay.REJECTION_LIMIT
    assert state["rejection_codes"] == {"http_400:invalid_reasoning_effort": 2}
    assert state["failed"] == 0 and result["failures"] == {}
    assert sum(1 for _, level in fake.calls if level == "medium") == replay.REJECTION_LIMIT
    for row in result["results"]:
        assert row["pass_rate"]["medium"] is None and row["rejected_levels"] == ["medium"]


def test_one_isolated_rejection_does_not_kill_a_level():
    seen = {"n": 0}

    def rule(item, level):
        seen["n"] += 1
        if seen["n"] == 1:
            raise replay.TargetRejected(422, "x")
        return _passing(item, level)
    result = _run(Fake(rule), limit=2)
    assert result["level_status"]["low"]["accepted"] > 0
    assert result["level_status"]["low"]["rejected"] == 1


def test_target_errors_count_as_failed_attempts_and_fatal_aborts():
    def flaky(item, level):
        raise replay.TargetError("timeout")
    result = _run(Fake(flaky), limit=1)
    assert result["failures"] == {"timeout": 12} and result["dropped"] == 1

    def fatal(item, level):
        raise replay.TargetError("http_401", fatal=True)
    fake = Fake(fatal)
    result = _run(fake, limit=5)
    assert result["aborted"] == "fatal_http_401" and len(fake.calls) == 1 and result["items"] == 0


def test_levels_subset_is_honoured():
    fake = Fake(_passing)
    result = replay.run_replay(corpus, "e" * 64, ROUTE, ("low", "high"), 3, fake, now=NOW,
                               limit=2)
    assert {level for _, level in fake.calls} == {"low", "high"}
    assert result["levels"] == ["low", "high"] and len(fake.calls) == 2 * 2 * 3


# ── result writer and privacy ────────────────────────────────────────────────

def test_result_holds_ids_and_numbers_only(tmp_path):
    result = _run(Fake(_passing), limit=3)
    path = replay.write_result(result, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert path.name == "effort-replay-m-test-eeeeeeee-20261021.json"
    document = json.loads(text)
    assert document["schema"] == "hermes-adaptive-effort.effort-replay.v1"
    assert set(document["results"][0]) == {
        "id", "expected", "pass_rate", "label", "dropped", "non_monotonic", "floor",
        "rejected_levels", "tokens", "reasoning_tokens"}
    assert not KEY_LIKE.search(text) and "http" not in text.replace("http_", "")
    for item in corpus["items"]:
        assert item["prompt"] not in text


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_dry_run_sends_nothing_and_reports_planned_calls(tmp_path, capsys):
    fake = Fake(_passing)
    code = replay.main(["--out-dir", str(tmp_path), "--limit", "5"], call_target=fake)
    out = capsys.readouterr().out
    assert code == 0 and fake.calls == [] and list(tmp_path.iterdir()) == []
    assert "planned target calls: 60" in out and "low,medium,high,max" in out


def test_default_route_levels_match_the_registry():
    assert effort.route_choice_levels("opencode-go", "deepseek-v4.1-flash",
                                      "chat_completions") == LEVELS


@pytest.mark.parametrize("argv", [
    ["--send", "--limit", "2"],                                     # no guard
    ["--send", "--max-cost", "1"],                                  # no prices
    ["-n", "2"], ["--threshold", "0"], ["--levels", "xhigh"],
    ["--model", "unknown-model"], ["--api-mode", "codex_responses"],
])
def test_bad_arguments_exit_2_without_calls(argv, tmp_path, capsys):
    fake = Fake(_passing)
    code = replay.main(argv + ["--out-dir", str(tmp_path)], call_target=fake)
    assert code == 2 and fake.calls == []


def test_send_with_injected_target_writes_a_file(tmp_path, capsys):
    fake = Fake(_passing)
    code = replay.main(["--send", "--max-calls", "1000", "--limit", "2", "--levels", "low,high",
                        "--out-dir", str(tmp_path)], call_target=fake, now=NOW)
    out = capsys.readouterr().out
    assert code == 0 and len(fake.calls) == 12
    assert len(list(tmp_path.glob("effort-replay-*.json"))) == 1 and "wrote" in out


def test_send_without_key_env_refuses_before_any_request(tmp_path, capsys, monkeypatch):
    code = replay.main(["--send", "--max-calls", "5", "--out-dir", str(tmp_path)])
    assert code == 2 and "--key-env" in capsys.readouterr().out
    code = replay.main(["--send", "--max-calls", "5", "--key-env", "NO_SUCH_VAR_FOR_TEST",
                        "--out-dir", str(tmp_path)])
    assert code == 2 and list(tmp_path.iterdir()) == []


def test_aborted_run_exits_nonzero_and_still_writes(tmp_path, capsys):
    code = replay.main(["--send", "--max-calls", "5", "--limit", "3",
                        "--out-dir", str(tmp_path)], call_target=Fake(_passing), now=NOW)
    assert code == 1 and "ABORTED: max_calls" in capsys.readouterr().out
    assert json.loads(next(tmp_path.iterdir()).read_text())["aborted"] == "max_calls"


# ── real transport against a fake opener ─────────────────────────────────────

class _Response:
    def __init__(self, document, status=200):
        self._body, self.status = json.dumps(document).encode(), status

    def read(self, *a):
        return self._body

    def close(self):
        pass


def _http_error(status, document):
    return urllib.error.HTTPError("https://target.invalid/x", status, "err", {},
                                  io.BytesIO(json.dumps(document).encode()))


def _target(transport):
    return replay.make_opencode_go_target("https://target.invalid/v1/chat/completions",
                                          "k" * 12, "m-test", transport=transport)


def test_transport_builds_the_paired_effort_body_and_parses_usage():
    seen = {}

    def transport(request, timeout):
        seen["body"] = json.loads(request.data)
        seen["auth"] = request.get_header("Authorization")
        return _Response({"choices": [{"message": {"content": "42"}}],
                          "usage": {"prompt_tokens": 3, "completion_tokens": 4}})
    text, usage = _target(transport)("prompt text", "high")
    assert text == "42" and usage["completion_tokens"] == 4
    assert seen["body"]["reasoning_effort"] == "high"
    assert seen["body"]["thinking"] == {"type": "enabled"}
    assert seen["body"]["model"] == "m-test" and seen["auth"] == "Bearer " + "k" * 12


@pytest.mark.parametrize("status", [400, 422])
def test_transport_classifies_effort_rejection(status):
    def transport(request, timeout):
        raise _http_error(status, {"error": {"code": "Bad Effort!", "message": "secret prompt"}})
    with pytest.raises(replay.TargetRejected) as caught:
        _target(transport)("p", "medium")
    assert caught.value.status == status and caught.value.code == "badeffort"
    assert "secret prompt" not in str(caught.value)


@pytest.mark.parametrize("status,fatal", [(401, True), (403, True), (429, False), (500, False)])
def test_transport_other_http_errors_are_failures(status, fatal):
    def transport(request, timeout):
        raise _http_error(status, {})
    with pytest.raises(replay.TargetError) as caught:
        _target(transport)("p", "low")
    assert caught.value.code == f"http_{status}" and caught.value.fatal is fatal


def test_transport_never_retries_and_maps_network_errors():
    calls = []

    def transport(request, timeout):
        calls.append(1)
        raise urllib.error.URLError(TimeoutError())
    with pytest.raises(replay.TargetError) as caught:
        _target(transport)("p", "low")
    assert caught.value.code == "timeout" and len(calls) == 1
    with pytest.raises(replay.TargetError) as caught:
        _target(lambda r, t: _Response({"choices": []}))("p", "low")
    assert caught.value.code == "malformed_response"


def test_transport_default_opener_does_not_follow_redirects():
    http = import_plugin("scorer_http")
    assert any(isinstance(h, http.NoRedirectHandler) for h in http._get_opener().handlers)
