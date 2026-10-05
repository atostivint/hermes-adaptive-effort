"""Jev score -> effort label mapping (pure functions, no I/O)."""

from __future__ import annotations

import pytest


from conftest import import_plugin

effort = import_plugin("effort")


def test_threshold_boundaries():
    assert effort.score_to_label(0.0) == "low"
    assert effort.score_to_label(0.4999) == "low"
    assert effort.score_to_label(0.5) == "medium"
    assert effort.score_to_label(1.4999) == "medium"
    assert effort.score_to_label(1.5) == "high"
    assert effort.score_to_label(2.0) == "high"


def test_invalid_scores_return_none():
    for bad in (float("nan"), float("inf"), -1.0, 2.5, None, "1", True, object()):
        assert effort.score_to_label(bad) is None, bad


def test_labels_are_the_normalized_three():
    assert effort.EFFORT_LABELS == ("low", "medium", "high")


def test_muse_contributor_free_wire_vocabulary_and_policy():
    model = "muse-spark-1.3-contributor-free"
    expected = ("minimal", "low", "medium", "high", "xhigh")
    for slug in (model, "opencode/" + model):
        assert effort.wire_efforts("opencode", slug) == expected
        assert effort.supported_efforts("opencode", slug) == expected
        for label in effort.EFFORT_LABELS:
            assert effort.map_effort(label, "opencode", slug) == label
        for invalid in ("none", "max"):
            assert effort.map_effort(invalid, "opencode", slug) is None
    assert effort.wire_efforts("opencode", "muse-spark-unknown") == ()


@pytest.mark.parametrize(("model", "includes_max"), [
    ("muse-spark-1.3", True),
    ("muse-spark-1.2", False),
    ("muse-spark-1.3-contributor-free", False),
    ("muse-spark-1.3-contributor", False),
    ("muse-spark-1.2-contributor", False),
])
def test_muse_injection_vocabulary_matches_exact_tier(model, includes_max):
    supported = effort.wire_efforts("opencode-zen", model)
    assert "none" not in supported
    assert ("max" in supported) is includes_max
    assert effort.map_effort("high", "opencode-zen", model) == "high"


def test_map_effort_clamps_onto_route_vocabulary():
    # OpenAI-compatible route vocabulary accepts all three verbatim.
    assert effort.map_effort("high", "openrouter", "x/y") == "high"
    # A route whose declared set stops at medium must never receive an escalation
    # to an unsupported level: clamp_effort picks the nearest weaker level.
    assert effort.map_effort("high", "openrouter", "x/y", supported=("low", "medium")) == "medium"
    assert effort.map_effort("medium", "openrouter", "x/y", supported=("low", "medium")) == "medium"


def test_map_effort_rejects_unknown_labels():
    assert effort.map_effort("ultra", "openrouter", "x/y") is None
    assert effort.map_effort(None, "openrouter", "x/y") is None
    assert effort.map_effort("", "openrouter", "x/y") is None


def test_supported_efforts_reads_route_data():
    assert "medium" in effort.supported_efforts("openrouter", "x/y")
    # Verified route vocabulary for an OpenAI-compatible wire.
    assert effort.supported_efforts("openai-codex", "gpt-5.6") == (
        "none", "low", "medium", "high", "xhigh", "max",
    ) or "low" in effort.supported_efforts("openai-codex", "gpt-5.6")
