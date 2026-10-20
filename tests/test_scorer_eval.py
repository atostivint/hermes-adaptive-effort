"""Scorer evaluation corpus, script and ``GET /eval`` summary: network-free, fakes only."""

from __future__ import annotations

import datetime
import importlib.util
import json
import re
import sys

import pytest

from conftest import PLUGIN_DIR, import_plugin
import test_plugin_api as plugin_api_tests

effort = import_plugin("effort")
middleware = import_plugin("middleware")
scorers = import_plugin("scorers")

SCRIPT = PLUGIN_DIR / "scripts" / "run_scorer_eval.py"
CORPUS = PLUGIN_DIR / "tests" / "fixtures" / "scorer_eval_corpus.json"
KEY_LIKE = re.compile(r"(sk-[A-Za-z0-9]{8,}|Bearer\s+\S+|api[_-]?key|authorization)", re.I)


def _load_script():
    name = "run_scorer_eval_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()
corpus = json.loads(CORPUS.read_text(encoding="utf-8"))


def test_corpus_shape():
    items = corpus["items"]
    assert corpus["version"]
    assert 50 <= len(items) <= 70
    assert len({item["id"] for item in items}) == len(items)
    assert script.validate_corpus(corpus, effort.CHOICE_LEVELS) == []
    for level in effort.CHOICE_LEVELS:
        assert sum(1 for item in items if item["expected"] == level) >= 8
    assert not {"none", "ultra"} & {item["expected"] for item in items}
    assert {tag for item in items for tag in item["tags"]} == set(script.KNOWN_TAGS)


def test_validate_corpus_reports_problems():
    bad = {"version": "x", "items": [
        {"id": "a", "prompt": " ", "expected": "ultra", "tags": ["nope"]},
        {"id": "a", "prompt": "p", "expected": "low", "tags": ["ambiguous"]}]}
    problems = script.validate_corpus(bad, effort.CHOICE_LEVELS)
    assert len(problems) >= 4


def test_expectations_clamp_like_the_plugin():
    narrow = ("low", "medium", "high")
    for level in effort.CHOICE_LEVELS:
        assert script.clamp_expectation(effort, level, narrow) == effort.map_named_choice(
            level, narrow)
        assert script.clamp_expectation(effort, level, narrow) in narrow
    assert script.clamp_expectation(effort, "low", effort.CHOICE_LEVELS) == "low"


def test_parse_choices_accepts_subsets_in_scale_order_and_rejects_others():
    assert script.parse_choices("high,low", effort.CHOICE_LEVELS) == ("low", "high")
    for bad in ("", "none", "low,ultra"):
        with pytest.raises(ValueError):
            script.parse_choices(bad, effort.CHOICE_LEVELS)


def test_percentile_nearest_rank():
    values = [10.0, 20.0, 30.0, 40.0]
    assert script.percentile(values, 50) == 20.0
    assert script.percentile(values, 95) == 40.0
    assert script.percentile([], 50) is None


class _Clock:
    """Each call advances 0.05 s, so every timed classification measures 50 ms."""

    def __init__(self, step=0.05):
        self.value, self.step = 0.0, step

    def __call__(self):
        self.value += self.step
        return self.value


def _fake_classifier(answers):
    """Factory whose client answers from a prompt -> (choice, failure) table."""
    class Client:
        def classify_effort_detail(self, prompt, choices):
            return answers[prompt]

    return lambda *a, **k: Client()


def _settings(**over):
    settings = dict(middleware.DEFAULTS)
    settings.update({"scorer_provider": "custom", "scorer_model": "m",
                     "custom_endpoint": "https://example.test/v1?token=SECRET-VALUE",
                     "timeout_s": 3.0})
    settings.update(over)
    return settings


def _mini_corpus():
    items = [
        {"id": "a", "prompt": "p-a", "expected": "minimal", "tags": ["ambiguous"]},
        {"id": "b", "prompt": "p-b", "expected": "xhigh", "tags": ["ambiguous"]},
        {"id": "c", "prompt": "p-c", "expected": "high", "tags": ["ambiguous"]},
        {"id": "d", "prompt": "p-d", "expected": "low", "tags": ["ambiguous"]},
    ]
    return {"version": "t1", "items": items}


def test_writer_metrics_with_fake_classifier_and_injected_clock(monkeypatch):
    narrow = ("low", "medium", "high")
    monkeypatch.setattr(middleware, "_classifier_factory", _fake_classifier({
        "p-a": ("low", None), "p-b": ("medium", None),
        "p-c": (None, "timeout"), "p-d": ("high", None)}))
    result = script.run_evaluation(
        _mini_corpus(), "d" * 64, _settings(), narrow, effort_module=effort,
        middleware=middleware, scorers=scorers, clock=_Clock(),
        now=datetime.datetime(2026, 10, 14, 12, 0, 0, tzinfo=datetime.timezone.utc))
    assert result["schema"] == "hermes-adaptive-effort.scorer-eval.v1"
    assert result["items"] == 4 and result["correct"] == 1  # only p-a: minimal -> low
    assert result["accuracy"] == 0.25
    assert result["failures"] == {"timeout": 1}
    assert result["p50_ms"] == pytest.approx(50.0) and result["p95_ms"] == pytest.approx(50.0)
    by_id = {row["id"]: row for row in result["results"]}
    assert by_id["b"]["expected"] == "xhigh"
    assert by_id["b"]["expected_clamped"] == effort.map_named_choice("xhigh", narrow)
    assert by_id["c"]["answered"] is None and by_id["c"]["failure"] == "timeout"
    assert result["confusion"][by_id["c"]["expected_clamped"]]["failed"] == 1
    assert result["confusion"]["low"]["low"] == 1
    assert result["run_at"] == "2026-10-14T12:00:00Z"
    assert script.result_filename(result) == "scorer-eval-custom-20261014T120000Z.json"
    assert "example.test" in result["endpoint"] and "SECRET-VALUE" not in json.dumps(result)
    assert not any("p-a" in json.dumps(row) for row in result["results"])  # ids only


def test_invalid_answers_count_as_failures(monkeypatch):
    monkeypatch.setattr(middleware, "_classifier_factory", _fake_classifier(
        {"p-a": ("ultra", None), "p-b": (None, None), "p-c": ("high", None), "p-d": ("low", None)}))
    result = script.run_evaluation(
        _mini_corpus(), "d" * 64, _settings(), effort.CHOICE_LEVELS, effort_module=effort,
        middleware=middleware, scorers=scorers, clock=_Clock())
    assert result["correct"] == 2
    assert result["failures"] == {"classifier_error": 1, "malformed_response": 1}


def test_without_send_makes_zero_classifier_calls(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda *a, **k: calls.append(1))
    code = script.main(["--provider", "custom", "--out-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0 and calls == []
    assert f"planned scorer calls: {len(corpus['items'])}" in out
    assert list(tmp_path.iterdir()) == []


def test_corpus_flag_default_is_the_scorer_corpus():
    parser = script.make_parser(scorers.PROVIDERS)
    assert parser.parse_args([]).corpus == str(script.CORPUS_PATH)
    assert parser.parse_args(["--corpus", "x.json"]).corpus == "x.json"
    loaded, digest = script.load_corpus(script.CORPUS_PATH)
    assert loaded == corpus and len(digest) == 64


def test_corpus_flag_selects_another_corpus(tmp_path, capsys, monkeypatch):
    # clamping is not under test here (and needs the Hermes tree); the corpus choice is
    monkeypatch.setattr(script, "clamp_expectation", lambda module, expected, choices: expected)
    replay_corpus = PLUGIN_DIR / "tests" / "fixtures" / "effort_replay_corpus.json"
    other = json.loads(replay_corpus.read_text(encoding="utf-8"))
    assert script.main(["--provider", "custom", "--out-dir", str(tmp_path)]) == 0
    assert f"planned scorer calls: {len(corpus['items'])}" in capsys.readouterr().out
    assert script.main(["--provider", "custom", "--corpus", str(replay_corpus)]) == 0
    out = capsys.readouterr().out
    assert f"planned scorer calls: {len(other['items'])}" in out
    assert script.main(["--provider", "custom", "--corpus", str(tmp_path / "missing.json")]) == 2
    assert "corpus unreadable" in capsys.readouterr().out


def test_send_writes_file_and_never_prints_or_writes_key_like_strings(monkeypatch, tmp_path, capsys):
    fake_key = "".join(["sk", "-", "leak", "-test-", "0123456789"])  # assembled: not a literal secret
    monkeypatch.setenv("OPENROUTER_API_KEY", fake_key)
    answers = {item["prompt"]: (item["expected"], None) for item in corpus["items"]}
    monkeypatch.setattr(middleware, "_classifier_factory", _fake_classifier(answers))
    code = script.main(["--provider", "custom", "--scorer-model", "m", "--send", "--limit", "5",
                        "--out-dir", str(tmp_path)], clock=_Clock())
    out = capsys.readouterr().out
    assert code == 0
    files = list(tmp_path.glob("scorer-eval-custom-*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    document = json.loads(text)
    assert document["items"] == 5 and document["accuracy"] == 1.0
    assert document["corpus_sha256"] and document["corpus_version"] == corpus["version"]
    for blob in (text, out):
        assert not KEY_LIKE.search(blob) and fake_key not in blob
    for item in corpus["items"]:
        assert item["prompt"] not in text and item["prompt"] not in out


# ── GET /eval summary ───────────────────────────────────────────────────────

api = plugin_api_tests.api


def _result(provider="jev", model="m1", run_at="2026-10-14T10:00:00Z", **over):
    document = {"schema": "hermes-adaptive-effort.scorer-eval.v1", "provider": provider,
                "scorer_model": model, "choices": ["low", "high"], "items": 10,
                "accuracy": 0.7, "p50_ms": 100.0, "p95_ms": 300.0, "failures": {},
                "run_at": run_at, "corpus_version": "v1", "corpus_sha256": "x" * 64,
                "confusion": {"low": {"low": 1}},
                "results": [{"id": "c001", "answered": "low"}], "endpoint": "https://e.test"}
    document.update(over)
    return document


@pytest.fixture
def eval_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_EVAL_DIR_PROVIDER", lambda: tmp_path)
    return tmp_path


def test_eval_payload_is_empty_without_directory_or_results(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_EVAL_DIR_PROVIDER", lambda: tmp_path / "missing")
    payload = api.get_eval_payload()
    assert payload["schema"] == "hermes-adaptive-effort.scorer-eval-summary.v1"
    assert payload["evaluations"] == []

    def boom():
        raise RuntimeError("no hermes")

    monkeypatch.setattr(api, "_EVAL_DIR_PROVIDER", boom)
    assert api.get_eval_payload()["evaluations"] == []


def test_eval_payload_newest_per_scorer_skips_malformed_and_exposes_only_summary(eval_dir):
    (eval_dir / "scorer-eval-jev-20261013T100000Z.json").write_text(
        json.dumps(_result(run_at="2026-10-13T10:00:00Z", accuracy=0.1)))
    (eval_dir / "scorer-eval-jev-20261014T100000Z.json").write_text(json.dumps(_result()))
    (eval_dir / "scorer-eval-custom-20261014T100000Z.json").write_text(
        json.dumps(_result(provider="custom", model="m2", run_at="2026-10-14T09:00:00Z")))
    (eval_dir / "scorer-eval-bad-1.json").write_text("{not json")
    (eval_dir / "scorer-eval-bad-2.json").write_text(json.dumps({"schema": "other"}))
    (eval_dir / "scorer-eval-bad-3.json").write_text(json.dumps(_result(accuracy="high")))
    rows = api.get_eval_payload()["evaluations"]
    assert [(r["provider"], r["run_at"]) for r in rows] == [
        ("jev", "2026-10-14T10:00:00Z"), ("custom", "2026-10-14T09:00:00Z")]
    assert rows[0]["accuracy"] == 0.7
    for row in rows:
        assert set(row) == set(api._EVAL_FIELDS)
    assert "results" not in json.dumps(rows) and "confusion" not in json.dumps(rows)
