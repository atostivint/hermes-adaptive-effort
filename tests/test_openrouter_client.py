"""OpenRouter adapter: bounded JSON scoring, provider credentials, and fail-open errors."""

from __future__ import annotations

import json
import urllib.error

import pytest

from tests.conftest import import_plugin

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


def response(content):
    return {"choices": [{"message": {"content": content}}]}


def make_client(**kwargs):
    kwargs.setdefault("model", "anthropic/claude-3.5-sonnet")
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("transport", FakeTransport(response('{"score": 1.25}')))
    return client_module.OpenRouterClient(**kwargs)


def test_valid_openrouter_response_returns_numeric_score():
    client = make_client()
    assert client.classify_detail("prompt") == (pytest.approx(1.25), None)


def test_prompt_text_never_appears_in_debug_logs(caplog):
    marker = "private-openrouter-prompt-marker"
    client = make_client()
    client.classify(marker)
    assert marker not in caplog.text


@pytest.mark.parametrize("content", [
    "not json", "{}", "[]", '{"score": true}', '{"score": "1"}',
    '{"score": -0.1}', '{"score": 2.1}', '{"score": NaN}',
])
def test_malformed_or_out_of_range_answers_fail_open(content):
    client = make_client(transport=FakeTransport(response(content)))
    assert client.classify_detail("prompt") == (None, "malformed_response")


def test_openrouter_request_is_bounded_and_uses_only_the_configured_model():
    transport = FakeTransport(response('{"score": 0}'))
    client = make_client(transport=transport, max_prompt_chars=64, timeout=2.5)
    assert client.classify("x" * 500) == 0.0
    call = transport.calls[0]
    body = json.loads(call["body"])
    assert call["url"] == client_module.DEFAULT_ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["timeout"] == pytest.approx(2.5)
    assert body["model"] == "anthropic/claude-3.5-sonnet"
    assert len(body["messages"][1]["content"]) <= 64
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == client_module.MAX_COMPLETION_TOKENS == 32
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"


def test_missing_key_never_builds_a_request():
    transport = FakeTransport(response('{"score": 1}'))
    client = make_client(api_key="", key_reader=lambda: "", transport=transport)
    assert client.classify_detail("prompt") == (None, "credential_missing")
    assert transport.calls == []


def test_model_is_required_even_when_a_key_is_available():
    transport = FakeTransport(response('{"score": 1}'))
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
