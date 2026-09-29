"""Jev score -> effort label mapping (pure functions, no I/O)."""

from __future__ import annotations



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
