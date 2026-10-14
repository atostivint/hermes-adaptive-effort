"""OpenRouter Decisions adapter: System One rubric, provider credentials, and fail-open errors."""

from __future__ import annotations

import json
import urllib.error

import pytest

from conftest import import_plugin

client_module = import_plugin("openrouter_client")


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append({
            "url": request.full_url,
            "headers": dict(request.header_items()),
            "body": request.data,
            "timeout": timeout,
        })
        if self.error:
            raise self.error
        return self.payload


CHOICES = ("low", "high", "max")


def response(score):
    return {"answers": {"effort": {"type": "score", "score": score,
                                   "probabilities": [0.1, 0.8, 0.1]}},
            "model": "typesafe/jev-1.13-2026-10-01"}


def choice_response(value):
    return {"answers": {"effort": {"type": "choice", "choice": value,
                                   "probabilities": {"low": 0.1, "high": 0.8, "max": 0.1}}}}


def make_client(**kwargs):
    kwargs.setdefault("model", "typesafe/jev-1.13")
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("transport", FakeTransport(response(1.25)))
    return client_module.OpenRouterClient(**kwargs)


def test_valid_openrouter_response_returns_numeric_score():
    client = make_client()
    assert client.classify_detail("prompt") == (pytest.approx(1.25), None)


def test_prompt_text_never_appears_in_debug_logs(caplog):
    marker = "private-openrouter-prompt-marker"
    client = make_client()
    client.classify(marker)
    assert marker not in caplog.text


@pytest.mark.parametrize("payload", [
    {}, [], {"answers": {}}, {"answers": {"effort": {}}},
    {"answers": {"effort": {"type": "refusal"}}},
    {"answers": {"effort": {"score": True}}}, {"answers": {"effort": {"score": "1"}}},
    {"answers": {"effort": {"score": -0.1}}}, {"answers": {"effort": {"score": 2.1}}},
    {"answers": {"effort": {"score": float("nan")}}},
    {"choices": [{"message": {"content": '{"score": 1}'}}]},
])
def test_malformed_or_out_of_range_answers_fail_open(payload):
    client = make_client(transport=FakeTransport(payload))
    assert client.classify_detail("prompt") == (None, "malformed_response")


@pytest.mark.parametrize("payload", [
    choice_response("medium"), choice_response(1), {"answers": {"effort": {"choice": "low"}}},
    {"answers": {"effort": {"type": "refusal"}}}, {"answers": {}}, {},
])
def test_choice_outside_the_route_list_or_malformed_fails_open_without_retry(payload):
    transport = FakeTransport(payload)
    client = make_client(transport=transport)
    assert client.classify_effort_detail("prompt", CHOICES) == (None, "malformed_response")
    assert len(transport.calls) == 1


def test_named_choice_request_and_response():
    transport = FakeTransport(choice_response("high"))
    client = make_client(transport=transport, classification_instructions="Prefer low.")
    assert client.classify_effort_detail("prompt", CHOICES) == ("high", None)
    question = json.loads(transport.calls[0]["body"])["questions"]["effort"]
    assert question["type"] == "choice"
    assert tuple(question["criteria"]) == CHOICES
    assert "Prefer low." in question["instructions"]


def test_openrouter_request_is_a_bounded_decisions_body_with_zdr_preferences():
    transport = FakeTransport(response(0))
    client = make_client(transport=transport, max_prompt_chars=64, timeout=2.5,
                         classification_instructions="Prefer low.")
    assert client.classify("x" * 500) == 0.0
    call = transport.calls[0]
    body = json.loads(call["body"])
    assert call["url"] == client_module.DEFAULT_ENDPOINT == \
        "https://openrouter.ai/api/alpha/decisions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["timeout"] == pytest.approx(2.5)
    assert set(body) == {"model", "state", "questions", "provider"}
    assert body["model"] == "typesafe/jev-1.13"
    assert len(body["state"]["prompt"]) <= 64
    assert body["questions"]["effort"]["type"] == "score"
    assert "Prefer low." in body["questions"]["effort"]["instructions"]
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}
    for chat_field in ("messages", "max_tokens", "response_format", "temperature", "stream"):
        assert chat_field not in body


def test_missing_key_never_builds_a_request():
    transport = FakeTransport(response(1))
    client = make_client(api_key="", key_reader=lambda: "", transport=transport)
    assert client.classify_detail("prompt") == (None, "credential_missing")
    assert transport.calls == []


def test_model_is_required_even_when_a_key_is_available():
    transport = FakeTransport(response(1))
    client = make_client(model="", transport=transport)
    assert client.classify_detail("prompt") == (None, "model_missing")
    assert transport.calls == []


def test_http_and_timeout_errors_are_reported_without_response_content():
    http = urllib.error.HTTPError(client_module.DEFAULT_ENDPOINT, 429, "rate limited", {}, None)
    cases = [(http, "http_error"), (TimeoutError("timed out"), "timeout")]
    for error, reason in cases:
        client = make_client(transport=FakeTransport(error=error))
        assert client.classify_detail("private prompt text") == (None, reason)


def test_credential_status_reads_environment_without_returning_secret(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-test-key")
    assert client_module.credential_present()
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    assert not client_module.credential_present()
