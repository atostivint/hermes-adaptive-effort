"""All configured scorer protocols carry and strictly validate named effort choices."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

jev = import_plugin("jev_client")
decisions = import_plugin("openai_decision_client")
cloudflare = import_plugin("cloudflare_client")
openrouter = import_plugin("openrouter_client")
custom = import_plugin("custom_client")

CHOICES = ("low", "high", "max")
ACCOUNT = "0123456789abcdef0123456789abcdef"


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(request)
        return self.payload


def response(provider, value):
    if provider in {"jev", "custom-systemone"}:
        return {"answers": {"effort": {"type": "choice", "choice": value}}}
    if provider == "cloudflare":
        return {"success": True, "errors": [], "result": {
            "answers": {"effort": {"type": "choice", "choice": value}}}}
    if provider == "decisions":
        return {"answers": [{"name": "effort", "type": "choice", "choice": value}]}
    return {"choices": [{"message": {"content": json.dumps({"effort": value})}}]}


def make_client(provider, transport):
    if provider == "jev":
        return jev.JevClient(api_key="key", transport=transport)
    if provider == "decisions":
        return decisions.OpenAIDecisionClient(api_key="key", transport=transport)
    if provider == "cloudflare":
        return cloudflare.CloudflareClient(account_id=ACCOUNT, api_key="key", transport=transport)
    if provider == "openrouter":
        return openrouter.OpenRouterClient(model="scorer/model", api_key="key",
                                           transport=transport)
    if provider == "custom-systemone":
        return custom.CustomClient(endpoint="https://scorer.example/score", model="scorer",
                                   api_key="", transport=transport)
    return custom.CustomClient(endpoint="https://scorer.example/score", model="scorer",
                               api_format="chat_completions", transport=transport)


@pytest.mark.parametrize("provider", [
    "jev", "decisions", "cloudflare", "openrouter", "custom-systemone", "custom-chat",
])
def test_provider_sends_only_route_choices_and_reads_named_response(provider):
    transport = FakeTransport(response(provider, "max"))
    client = make_client(provider, transport)

    assert client.classify_effort_detail("bounded task", CHOICES) == ("max", None)
    body = json.loads(transport.calls[0].data.decode("utf-8"))
    if provider in {"jev", "cloudflare", "custom-systemone"}:
        question = body["questions"]["effort"]
        assert question["type"] == "choice"
        assert tuple(question["criteria"]) == CHOICES
    elif provider == "decisions":
        question = body["questions"][0]
        assert question["type"] == "choice"
        assert tuple(option["value"] for option in question["choices"]) == CHOICES
    else:
        prompt = body["messages"][0]["content"]
        assert '"effort":"<allowed level>"' in prompt
        assert "max:" in prompt and "xhigh:" not in prompt


@pytest.mark.parametrize("provider", [
    "jev", "decisions", "cloudflare", "openrouter", "custom-systemone", "custom-chat",
])
def test_provider_rejects_a_choice_outside_the_route_vocabulary(provider):
    transport = FakeTransport(response(provider, "medium"))
    client = make_client(provider, transport)

    assert client.classify_effort_detail("bounded task", CHOICES) == (
        None, "malformed_response")
    assert len(transport.calls) == 1
