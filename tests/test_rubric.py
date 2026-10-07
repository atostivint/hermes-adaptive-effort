"""Shared classification rubric and operator guidance remain bounded and fixed."""

from __future__ import annotations

import pytest

from conftest import import_plugin

rubric = import_plugin("rubric")


def test_classifier_guidance_is_trimmed_and_capped():
    guidance = "  " + ("x" * 2100) + "  "

    assert rubric.normalize_classification_instructions(guidance) == "x" * 2000
    assert rubric.normalize_classification_instructions(None) == ""


def test_systemone_question_adds_guidance_without_replacing_the_rubric():
    marker = "Prefer medium for routine internal tooling."
    question = rubric.questions_for(marker)["effort"]

    assert marker in question["instructions"]
    assert question["criteria"] == rubric.QUESTIONS["effort"]["criteria"]


def test_decisions_question_uses_ordered_levels_and_supplemental_guidance():
    guidance = "Use high when several independent constraints interact."
    question = rubric.decisions_question(guidance)

    assert question["type"] == "score"
    assert question["name"] == "effort"
    assert [level["label"] for level in question["levels"]] == ["low", "medium", "high"]
    assert [level["description"] for level in question["levels"]] == \
        rubric.QUESTIONS["effort"]["criteria"]
    assert "provided input" in question["instructions"]
    assert "state.prompt" not in question["instructions"]
    assert guidance in question["instructions"]


def test_decisions_score_requires_one_named_score_answer():
    assert rubric.decisions_score({"answers": [{
        "name": "effort", "type": "score", "score": 1.25,
        "probabilities": [{"value": 1, "label": "medium", "probability": 1.0}],
        "confidence": 1.0,
    }]}) == (1.25, None)


@pytest.mark.parametrize("score", [0, 0.5, 1.5, 2])
def test_decisions_score_accepts_the_shared_rubric_boundaries(score):
    assert rubric.decisions_score({"answers": [{
        "name": "effort", "type": "score", "score": score,
    }]}) == (float(score), None)


@pytest.mark.parametrize("payload", [
    None,
    {},
    {"answers": []},
    {"answers": [{"name": "effort", "type": "refusal"}]},
    {"answers": [{"name": "other", "type": "score", "score": 1}]},
    {"answers": [{"name": "effort", "type": "choice", "score": 1}]},
    {"answers": [{"name": "effort", "type": "score", "score": True}]},
    {"answers": [{"name": "effort", "type": "score", "score": "1"}]},
    {"answers": [{"name": "effort", "type": "score", "score": float("nan")}]},
    {"answers": [{"name": "effort", "type": "score", "score": 2.01}]},
    {"answers": [
        {"name": "effort", "type": "score", "score": 1},
        {"name": "extra", "type": "score", "score": 1},
    ]},
])
def test_decisions_score_rejects_refusals_malformed_answers_and_invalid_scores(payload):
    assert rubric.decisions_score(payload) == (None, "malformed_response")


def test_chat_prompt_keeps_the_fixed_score_contract_with_guidance():
    marker = "Prefer high for irreversible production changes."
    prompt = rubric.chat_system_prompt(marker)

    assert marker in prompt
    assert "0 for low, 1 for medium, or 2 for high" in prompt
    assert "only the numeric score field" in prompt


def test_target_context_instructions_do_not_anchor_to_the_observed_effort():
    systemone = rubric.questions_for()["effort"]["instructions"]
    decisions = rubric.decisions_question()["instructions"]
    chat = rubric.chat_system_prompt()

    for prompt in (systemone, decisions, chat):
        assert "baseline" in prompt
        assert "do not anchor" in prompt
        assert "does not verify" in prompt


def test_named_choice_prompts_only_include_the_route_levels():
    levels = ("low", "high", "max")
    one = rubric.questions_for(choices=levels)["effort"]
    decisions = rubric.decisions_question(choices=levels)
    chat = rubric.chat_system_prompt(choices=levels)

    assert one["type"] == "choice"
    assert list(one["criteria"]) == list(levels)
    assert decisions["type"] == "choice"
    assert [choice["value"] for choice in decisions["choices"]] == list(levels)
    assert '"effort":"<allowed level>"' in chat
    assert "xhigh:" not in chat
    assert "lowest listed effort" in chat


def test_native_choice_parsers_reject_refusal_extra_fields_and_unlisted_levels():
    choices = ("low", "high", "max")
    assert rubric.systemone_choice({"answers": {"effort": {
        "type": "choice", "choice": "max"}}}, choices) == ("max", None)
    assert rubric.systemone_choice({"answers": {"effort": {
        "type": "choice", "choice": "medium"}}}, choices) == (None, "malformed_response")
    assert rubric.decisions_choice({"answers": [{"name": "effort", "type": "choice",
        "choice": "high"}]}, choices) == ("high", None)
    assert rubric.decisions_choice({"answers": [{"name": "effort", "type": "refusal"}]},
                                   choices) == (None, "malformed_response")
    assert rubric.chat_completion_choice({"choices": [{"message": {"content":
        '{"effort":"high"}'}}]}, choices) == "high"
    assert rubric.chat_completion_choice({"choices": [{"message": {"content":
        '{"effort":"medium"}'}}]}, choices) is None
    assert rubric.chat_completion_choice({"choices": [{"message": {"content":
        '{"effort":"high","score":2}'}}]}, choices) is None
