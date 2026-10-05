"""Shared classification rubric and operator guidance remain bounded and fixed."""

from __future__ import annotations

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
    assert marker not in rubric.QUESTIONS["effort"]["instructions"]


def test_chat_prompt_keeps_the_fixed_score_contract_with_guidance():
    marker = "Prefer high for irreversible production changes."
    prompt = rubric.chat_system_prompt(marker)

    assert marker in prompt
    assert "0 for low, 1 for medium, or 2 for high" in prompt
    assert "only the numeric score field" in prompt
