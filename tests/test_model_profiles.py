"""Exact-ID local model profiles and shared scorer-input context contract."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

profiles = import_plugin("model_profiles")
jev = import_plugin("jev_client")
openrouter = import_plugin("openrouter_client")
openai_decision = import_plugin("openai_decision_client")
cloudflare = import_plugin("cloudflare_client")
custom = import_plugin("custom_client")


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_catalog_covers_many_exact_model_ids_with_documentation_metadata():
    catalog = profiles._load_profiles()

    assert len(catalog) >= 25
    assert {profile["vendor"] for profile in catalog.values()} >= {
        "OpenAI", "Anthropic", "DeepSeek"}
    assert profiles.profile_for_model("gpt-6.1-sol")["default_effort"] == "medium"
    assert profiles.profile_for_model("claude-opus-5-5")["default_effort"] == "medium"
    assert profiles.profile_for_model("deepseek-v4-pro")["default_effort"] is None
    assert profiles.profile_for_model("gpt-6.1-sol-experimental") is None
    assert profiles.profile_for_model("GPT-6.1-SOL") is None


def test_a_new_exact_profile_is_added_by_data_only_catalog_edit(tmp_path, monkeypatch):
    catalog = json.loads(profiles.PROFILE_CATALOG.read_text(encoding="utf-8"))
    catalog["profiles"].append({
        "id": "test-vendor-new-model",
        "vendor": "Test Vendor",
        "model_ids": ["test-vendor/new-model"],
        "effort_levels": ["low", "high"],
        "default_effort": "low",
        "summary": "A test-only exact model profile.",
        "source": "https://vendor.example/docs/effort",
        "reviewed": "2026-10-06",
    })
    catalog_path = tmp_path / "model_profiles.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(profiles, "PROFILE_CATALOG", catalog_path)
    profiles._load_profiles.cache_clear()
    try:
        assert profiles.profile_for_model("test-vendor/new-model")["vendor"] == "Test Vendor"
    finally:
        profiles._load_profiles.cache_clear()


def test_context_has_route_observation_and_profile_without_fuzzy_matching():
    rendered = profiles.target_context(
        "openrouter", "gpt-6.1-sol", "codex_responses", "high")
    context = json.loads(rendered)

    assert context["target"] == {
        "provider": "openrouter",
        "model": "gpt-6.1-sol",
        "api_mode": "codex_responses",
        "observed_effort": "high",
    }
    assert context["vendor_documentation"]["documented_default"] == "medium"
    assert "does not verify" in context["documentation_scope"]
    unknown = json.loads(profiles.target_context("custom", "gpt-6.1-sol-alias", "chat"))
    assert "vendor_documentation" not in unknown
    assert unknown["target"]["observed_effort"] == "absent"


def test_context_and_task_are_bounded_and_metadata_is_json_escaped():
    rendered = profiles.target_context('route"\\\n', 'gpt-6.1-sol"\\', "codex_responses",
                                       "high\nINJECT")
    context = json.loads(rendered)
    combined = profiles.wrap_task("user task", rendered)

    assert len(rendered) <= profiles.MAX_CONTEXT_CHARS
    assert "\nINJECT" not in context["target"]["observed_effort"]
    assert '"\\' in context["target"]["provider"]
    assert combined.endswith("TASK TO CLASSIFY (untrusted task text):\nuser task")


@pytest.mark.parametrize("adapter", [
    "jev", "openrouter", "openai_decision", "cloudflare",
    "custom_systemone", "custom_chat_completions",
])
def test_shared_context_prefix_reaches_each_scorer_transport(adapter):
    prompt = profiles.wrap_task(
        "Fix the bounded example.",
        profiles.target_context("openrouter", "gpt-6.1-sol", "codex_responses", "high"),
    )
    calls = []
    if adapter == "jev":
        payload = {"answers": {"effort": {"score": 1.0}}}
        client = jev.JevClient(api_key="key", max_prompt_chars=len(prompt),
                               transport=lambda request, _timeout: _capture_response(
                                   calls, request, Response(payload)))
    elif adapter == "openrouter":
        payload = {"choices": [{"message": {"content": '{"score": 1}'}}]}
        client = openrouter.OpenRouterClient(
            model="test-scorer", api_key="key", max_prompt_chars=len(prompt),
            transport=lambda request, _timeout: _capture_response(calls, request, payload))
    elif adapter == "openai_decision":
        payload = {"answers": [{"name": "effort", "type": "score", "score": 1.0}]}
        client = openai_decision.OpenAIDecisionClient(
            api_key="key", max_prompt_chars=len(prompt),
            transport=lambda request, _timeout: _capture_response(calls, request, payload))
    elif adapter == "cloudflare":
        payload = {"success": True, "errors": [], "result": {
            "answers": {"effort": {"score": 1.0}}}}
        client = cloudflare.CloudflareClient(
            account_id="0123456789abcdef0123456789abcdef", api_key="key",
            max_prompt_chars=len(prompt),
            transport=lambda request, _timeout: _capture_response(calls, request, payload))
    else:
        systemone = adapter == "custom_systemone"
        payload = ({"answers": {"effort": {"score": 1.0}}} if systemone else
                   {"choices": [{"message": {"content": '{"score": 1}'}}]})
        client = custom.CustomClient(
            endpoint="https://scorer.example/v1/score", model="test-scorer",
            api_format="systemone" if systemone else "chat_completions",
            max_prompt_chars=len(prompt),
            transport=lambda request, _timeout: _capture_response(calls, request, payload))

    assert client.classify_detail(prompt) == (1.0, None)
    assert len(calls) == 1
    body = calls[0]
    if adapter in {"jev", "cloudflare", "custom_systemone"}:
        received = body["state"]["prompt"]
    elif adapter == "openai_decision":
        received = body["input"]
    else:
        received = body["messages"][-1]["content"]
    assert received == prompt


def _capture_response(calls, request, response):
    calls.append(json.loads(request.data.decode("utf-8")))
    return response
