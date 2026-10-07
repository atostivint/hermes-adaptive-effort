"""OpenAI Decisions request construction, strict parsing, and fail-open behavior."""

from __future__ import annotations

import io
import json
import logging
import sys
import types
import urllib.error
import urllib.request

import pytest

from conftest import import_plugin

client_module = import_plugin("openai_decision_client")


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.closed = False

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.payload


def score_response(score=1.0):
    return {"answers": [{"name": "effort", "type": "score", "score": score,
                         "probabilities": [], "confidence": 1.0}],
            "model": "gpt-6-luna", "usage": {}}


def make_client(**kwargs):
    kwargs.setdefault("key_reader", lambda: "test-openai-key")
    kwargs.setdefault("transport", FakeTransport(score_response()))
    return client_module.OpenAIDecisionClient(**kwargs)


def test_posts_documented_decision_shape_with_shared_effort_levels():
    transport = FakeTransport(score_response(1.25))
    client = make_client(transport=transport, timeout=2.5)

    assert client.classify_detail("task text") == (pytest.approx(1.25), None)
    request, timeout = transport.calls[0]
    body = json.loads(request.data.decode())
    assert request.full_url == "https://api.openai.com/v1/decisions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-openai-key"
    assert request.get_header("Content-type") == "application/json"
    assert timeout == pytest.approx(2.5)
    assert set(body) == {"model", "input", "questions"}
    assert body["model"] == "gpt-6-luna"
    assert body["input"] == "task text"
    question = body["questions"][0]
    assert question["name"] == "effort"
    assert question["type"] == "score"
    assert [level["label"] for level in question["levels"]] == ["low", "medium", "high"]


def test_explicit_model_and_bounded_input_and_guidance_are_sent():
    transport = FakeTransport(score_response(1.0))
    guidance = "x" * (client_module.rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS + 10)
    client = make_client(model="operator-model", transport=transport,
                         max_prompt_chars=40, classification_instructions=guidance)
    long_prompt = "prefix " + ("task " * 80) + " suffix"

    assert client.classify_detail(long_prompt) == (pytest.approx(1.0), None)
    body = json.loads(transport.calls[0][0].data.decode())
    assert body["model"] == "operator-model"
    assert len(body["input"]) == 40
    instructions = body["questions"][0]["instructions"]
    assert instructions.endswith("x" * client_module.rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS)
    assert "x" * (client_module.rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS + 1) not in instructions


def test_missing_credential_and_invalid_input_make_no_transport_calls():
    transport = FakeTransport(score_response())
    key_calls = []
    client = make_client(transport=transport, key_reader=lambda: key_calls.append(True) or "")

    assert client.classify_detail("task") == (None, "credential_missing")
    assert client.classify_detail("  ") == (None, "invalid_prompt")
    assert len(key_calls) == 1
    assert transport.calls == []


def test_secret_scope_precedes_environment_then_environment_is_fallback(monkeypatch):
    secret_scope = types.ModuleType("agent.secret_scope")
    secret_scope.get_secret = lambda _key, _default: "scope-key"
    monkeypatch.setitem(sys.modules, "agent.secret_scope", secret_scope)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    assert client_module._default_key_reader() == "scope-key"
    secret_scope.get_secret = lambda _key, _default: ""
    assert client_module._default_key_reader() == "environment-key"


def test_credential_probe_does_not_make_a_request():
    transport = FakeTransport(score_response())
    assert client_module.credential_present(lambda: "key") is True
    assert client_module.credential_present(lambda: "") is False
    assert make_client(transport=transport).endpoint == client_module.DEFAULT_ENDPOINT
    assert transport.calls == []


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500, 503])
def test_http_status_errors_fail_open_without_retry(status):
    response = FakeResponse(score_response(), status=status)
    transport = FakeTransport(response)
    assert make_client(transport=transport).classify_detail("task") == (None, "http_error")
    assert len(transport.calls) == 1
    assert response.closed is True


def test_http_error_and_timeout_fail_open_without_retry():
    http_error = urllib.error.HTTPError(
        client_module.DEFAULT_ENDPOINT, 429, "rate limit", {}, io.BytesIO(b"private body"))
    for error, expected in ((http_error, "http_error"),
                            (TimeoutError("private timeout detail"), "timeout"),
                            (urllib.error.URLError(TimeoutError("private detail")), "timeout"),
                            (OSError("private network detail"), "transport_error")):
        transport = FakeTransport(error=error)
        assert make_client(transport=transport).classify_detail("task") == (None, expected)
        assert len(transport.calls) == 1


def test_http_error_body_is_closed_without_reading_it():
    body = io.BytesIO(b"private body")
    error = urllib.error.HTTPError(
        client_module.DEFAULT_ENDPOINT, 500, "private detail", {}, body)
    transport = FakeTransport(error=error)

    assert make_client(transport=transport).classify_detail("task") == (None, "http_error")
    assert body.closed is True


def test_transport_disables_redirects():
    handler = client_module._NoRedirectHandler()
    request = urllib.request.Request(client_module.DEFAULT_ENDPOINT, method="POST")
    assert handler.redirect_request(
        request, None, 307, "Temporary Redirect", {}, "https://other.example/") is None


@pytest.mark.parametrize("payload", [
    None,
    {},
    {"answers": []},
    {"answers": [{"name": "effort", "type": "refusal"}]},
    {"answers": [{"name": "other", "type": "score", "score": 1}]},
    {"answers": [{"name": "effort", "type": "choice", "score": 1}]},
    {"answers": [{"name": "effort", "type": "score", "score": True}]},
    {"answers": [{"name": "effort", "type": "score", "score": "1"}]},
    {"answers": [{"name": "effort", "type": "score", "score": float("inf")}]},
    {"answers": [{"name": "effort", "type": "score", "score": -0.01}]},
    {"answers": [{"name": "effort", "type": "score", "score": 2.01}]},
    {"answers": [{"name": "effort", "type": "score", "score": 1},
                  {"name": "unexpected", "type": "score", "score": 1}]},
])
def test_refused_malformed_and_invalid_answers_fail_open(payload):
    assert make_client(transport=FakeTransport(payload)).classify_detail("task") == (
        None, "malformed_response")


def test_invalid_json_and_unexpected_transport_errors_fail_open():
    invalid_json = FakeResponse(b"not json")
    assert make_client(transport=FakeTransport(invalid_json)).classify_detail("task") == (
        None, "malformed_response")
    transport = FakeTransport(error=RuntimeError("private provider detail"))
    assert make_client(transport=transport).classify_detail("task") == (
        None, "unexpected_error")
    assert len(transport.calls) == 1


def test_logs_do_not_include_prompt_credential_response_or_exception_details(caplog):
    caplog.set_level(logging.DEBUG)
    prompt = "private task text"
    key = "private-api-key"
    response_detail = "private provider response"
    transport = FakeTransport(error=urllib.error.HTTPError(
        client_module.DEFAULT_ENDPOINT, 500, response_detail, {},
        io.BytesIO(response_detail.encode())))
    client = client_module.OpenAIDecisionClient(
        api_key=key, transport=transport, max_prompt_chars=100)

    assert client.classify_detail(prompt) == (None, "http_error")
    assert prompt not in caplog.text
    assert key not in caplog.text
    assert response_detail not in caplog.text
