"""Replay comparison report: join, confusion matrices, policy arithmetic (offline)."""

from __future__ import annotations

import importlib.util
import json
import sys

import pytest

from conftest import PLUGIN_DIR

SCRIPTS = PLUGIN_DIR / "scripts"


def _load(name):
    module_name = f"{name}_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(SCRIPTS / f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


cmp = _load("compare_replay")
LEVELS = ["low", "medium", "high", "max"]
SHA = "a" * 64


def _row(ident, expected, label, rates, tokens):
    return {"id": ident, "expected": expected, "label": label, "dropped": label is None,
            "pass_rate": dict(zip(LEVELS, rates, strict=True)), "tokens": dict(zip(LEVELS, tokens, strict=True))}


def _replay():
    rows = [
        _row("a", "low", "low", (1.0, 1.0, 1.0, 1.0), (10, 20, 40, 80)),
        _row("b", "medium", "high", (0.0, 0.3333, 1.0, 1.0), (12, 22, 42, 82)),
        _row("c", "max", "medium", (0.0, 1.0, 1.0, 1.0), (14, 24, 44, 84)),
        _row("d", "low", None, (0.0, 0.0, 0.0, 0.0), (16, 26, 46, 86)),
        _row("e", "medium", "high", (0.0, None, 1.0, 1.0), (18, None, 48, 88)),
    ]
    return {"schema": cmp.REPLAY_SCHEMA, "target": {"provider": "p", "model": "m",
            "api_mode": "chat_completions"}, "corpus_version": "v", "corpus_sha256": SHA,
            "levels": LEVELS, "samples_per_level": 3, "items": 5, "dropped": 1,
            "non_monotonic": 0, "results": rows}


def _scorer(answers, sha=SHA):
    return {"schema": cmp.SCORER_SCHEMA, "provider": "jev", "scorer_model": "s",
            "corpus_sha256": sha,
            "results": [{"id": i, "answered": a} for i, a in answers.items()]}


def test_hand_vs_derived_confusion_and_direction():
    summary = cmp.compare(_replay())
    block = summary["hand_vs_derived"]
    # reference = derived label, answer = hand label; dropped item d is excluded
    assert block["items"] == 4 and block["exact"] == 1
    assert block["under"] == 2 and block["over"] == 1 and block["agreement"] == 0.25
    assert block["confusion"]["high"] == {"low": 0, "medium": 2, "high": 0, "max": 0}
    assert block["confusion"]["medium"]["max"] == 1 and block["confusion"]["low"]["low"] == 1


def test_policy_cost_arithmetic():
    policies = cmp.compare(_replay())["policies"]
    assert policies["always_high"] == {"items": 4, "missing": 0, "rejected": 0,
                                       "mean_pass_rate": 1.0, "mean_tokens": 43.5}
    # always_medium: item e has medium rejected by the provider -> counted, not averaged
    medium = policies["always_medium"]
    assert medium["items"] == 3 and medium["rejected"] == 1
    assert medium["mean_tokens"] == pytest.approx((20 + 22 + 24) / 3, abs=0.1)
    assert medium["mean_pass_rate"] == pytest.approx((1.0 + 0.3333 + 1.0) / 3, abs=1e-3)
    assert policies["derived"]["mean_pass_rate"] == 1.0
    assert policies["derived"]["mean_tokens"] == pytest.approx((10 + 42 + 24 + 48) / 4, abs=0.1)


def test_hand_policy_counts_rejected_picks():
    hand = cmp.compare(_replay())["policies"]["hand"]
    # e hand label medium is rejected (excluded); a, b, c are averaged at low/medium/max
    assert hand["items"] == 3 and hand["rejected"] == 1
    assert hand["mean_tokens"] == pytest.approx((10 + 22 + 84) / 3, abs=0.1)
    assert hand["mean_pass_rate"] == pytest.approx((1.0 + 0.3333 + 1.0) / 3, abs=1e-3)


def test_scorer_join_matrix_and_failures():
    scorer = _scorer({"a": "low", "b": "high", "c": "max", "d": "low", "e": None})
    summary = cmp.compare(_replay(), scorer)
    assert summary["scorer"]["joined"] == 4 and summary["scorer"]["failed"] == 1
    block = summary["scorer_vs_derived"]
    assert block["items"] == 3 and block["exact"] == 2 and block["over"] == 1
    assert block["confusion"]["medium"]["max"] == 1
    policy = summary["policies"]["scorer"]
    assert policy["missing"] == 1 and policy["items"] == 3
    assert policy["mean_tokens"] == pytest.approx((10 + 42 + 84) / 3, abs=0.1)


def test_unjoined_items_are_reported():
    summary = cmp.compare(_replay(), _scorer({"a": "low"}))
    assert summary["scorer"]["joined"] == 1 and summary["scorer"]["not_in_scorer_result"] == 3


def test_mismatched_inputs_are_rejected():
    with pytest.raises(ValueError, match="corpus mismatch"):
        cmp.compare(_replay(), _scorer({"a": "low"}, sha="b" * 64))
    with pytest.raises(ValueError):
        cmp.compare({"schema": "other"})
    with pytest.raises(ValueError):
        cmp.compare(_replay(), {"schema": "other"})


def test_cli_prints_report_and_writes_json(tmp_path, capsys):
    replay_path, scorer_path = tmp_path / "r.json", tmp_path / "s.json"
    replay_path.write_text(json.dumps(_replay()))
    scorer_path.write_text(json.dumps(_scorer({"a": "low", "b": "high"})))
    out_path = tmp_path / "summary.json"
    code = cmp.main([str(replay_path), "--scorer", str(scorer_path), "--json", str(out_path)])
    text = capsys.readouterr().out
    assert code == 0 and "synthetic tasks, one model, N samples per level" in text
    assert "always_high" in text and "scorer_vs_derived" not in text
    assert json.loads(out_path.read_text())["policies"]["scorer"]["items"] == 2


def test_cli_errors_exit_2(tmp_path, capsys):
    assert cmp.main([str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "b.json"
    bad.write_text("{}")
    assert cmp.main([str(bad)]) == 2
    assert "error:" in capsys.readouterr().out
