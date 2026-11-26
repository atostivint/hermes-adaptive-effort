"""OpenCode Go System One request shape, strict parsing, and fail-open behavior."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import types
import urllib.error

import pytest

from conftest import import_plugin

client_module = import_plugin("opencode_go_client")
scorer_http = import_plugin("scorer_http")
scorer_retry = import_plugin("scorer_retry")
rubric = import_plugin("rubric")


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
    return {"answers": {"effort": {"type": "score", "score": score}}, "model": "jev-1.14"}


def choice_response(choice="medium"):
    return {"answers": {"effort": {"type": "choice", "choice": choice}}}


def continuation_response(probability=0.75):
    return {"answers": {"continuation": {"type": "noul", "noul": probability}}}


def make_client(**kwargs):
    kwargs.setdefault("key_reader", lambda: "test-go-key")
    kwargs.setdefault("transport", FakeTransport(score_response()))
    return client_module.OpenCodeGoClient(**kwargs)


def headers_of(request):
    return {name.lower(): value for name, value in request.header_items()}


def test_posts_exact_systemone_request_shape_with_named_user_agent():
    transport = FakeTransport(score_response(1.25))
    client = make_client(transport=transport, timeout=2.5, max_prompt_chars=64)

    assert client.classify_detail("task text") == (pytest.approx(1.25), None)
    request, timeout = transport.calls[0]
    body = json.loads(request.data.decode())
    headers = headers_of(request)
    assert request.full_url == client_module.DEFAULT_ENDPOINT
    assert request.get_method() == "POST"
    assert headers["authorization"] == "Bearer test-go-key"
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"
    assert headers["user-agent"] == scorer_http.USER_AGENT
    assert scorer_http.USER_AGENT.startswith("hermes-adaptive-effort/")
    assert "x-opencode-session" not in headers
    assert timeout == pytest.approx(2.5)
    assert set(body) == {"model", "state", "questions"}
    assert body["model"] == "jev-1.14"
    assert body["state"] == {"prompt": "task text"}
    assert body["questions"] == rubric.questions_for()


def test_endpoint_is_fixed_and_known_ids_are_passed_through_unchanged():
    client = make_client()
    assert client.endpoint == "https://opencode.ai/inference/go/systemone/v1/systemone"
    assert client_module.DEFAULT_ENDPOINT == client.endpoint
    assert client.model == client_module.DEFAULT_MODEL == "jev-1.14"
    assert set(client_module.KNOWN_MODELS) == {
        "jev-1.14", "gemini-decision-1", "gpt-6-luna-decision"}

    for model in client_module.KNOWN_MODELS + ("operator-asserted-id",):
        transport = FakeTransport(score_response())
        explicit = make_client(model=model, transport=transport)
        assert explicit.classify("task") == 1.0
        assert json.loads(transport.calls[0][0].data.decode())["model"] == model

    assert make_client(model="  ").model == "jev-1.14"


def test_bounded_prompt_and_guidance_are_sent_with_the_shared_rubric():
    transport = FakeTransport(score_response(1.0))
    guidance = "x" * (rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS + 10)
    client = make_client(transport=transport, max_prompt_chars=40,
                         classification_instructions=guidance)
    long_prompt = "prefix " + ("task " * 80) + " suffix"

    assert client.classify_detail(long_prompt) == (pytest.approx(1.0), None)
    body = json.loads(transport.calls[0][0].data.decode())
    assert len(body["state"]["prompt"]) == 40
    assert body["questions"] == rubric.questions_for(guidance)


def test_named_choice_question_and_strict_choice_parsing():
    choices = ("low", "high")
    transport = FakeTransport(choice_response("high"))
    client = make_client(transport=transport)

    assert client.classify_effort_detail("task", choices) == ("high", None)
    body = json.loads(transport.calls[0][0].data.decode())
    assert body["questions"] == rubric.questions_for("", choices)
    assert body["questions"]["effort"]["type"] == "choice"
    assert set(body["questions"]["effort"]["criteria"]) == set(choices)

    reject = FakeTransport(choice_response("max"))
    assert make_client(transport=reject).classify_effort_detail("task", choices) == (
        None, "malformed_response")


def test_continuation_uses_the_systemone_noul_question():
    transport = FakeTransport(continuation_response(0.75))
    client = make_client(transport=transport)

    assert client.classify_continuation("follow-up") == (pytest.approx(0.75), None)
    body = json.loads(transport.calls[0][0].data.decode())
    assert body["questions"] == rubric.continuation_questions()
    assert body["questions"]["continuation"]["type"] == "noul"

    reject = FakeTransport(continuation_response(1.5))
    assert make_client(transport=reject).classify_continuation("task") == (
        None, "malformed_response")


def test_missing_credential_and_invalid_input_make_no_transport_calls():
    transport = FakeTransport(score_response())
    reads = []
    client = make_client(transport=transport,
                         key_reader=lambda: reads.append(True) or "")

    assert client.classify_detail("task") == (None, "credential_missing")
    assert client.classify_detail("  ") == (None, "invalid_prompt")
    assert client.classify_detail(None) == (None, "invalid_prompt")
    assert len(reads) == 1
    assert transport.calls == []


def test_explicit_api_key_is_used_without_a_key_reader():
    transport = FakeTransport(score_response())
    client = client_module.OpenCodeGoClient(
        api_key="  explicit-key  ", transport=transport,
        key_reader=lambda: pytest.fail("key reader must not run"))
    assert client.classify("task") == 1.0
    assert headers_of(transport.calls[0][0])["authorization"] == "Bearer explicit-key"


def test_primary_credential_then_legacy_name_resolve_secret_scope_before_environment(monkeypatch):
    seen = []
    scope = types.ModuleType("agent.secret_scope")

    def get_secret(name, default):
        seen.append(("scope", name))
        return {"OPENCODE_GO_API_KEY": "scope-primary"}.get(name, default)

    scope.get_secret = get_secret
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "env-primary")
    monkeypatch.setenv("OPENCODE_API_KEY", "env-legacy")

    assert client_module._default_key_reader() == "scope-primary"
    assert seen[0] == ("scope", "OPENCODE_GO_API_KEY")

    # A normal single-profile get_secret reads the environment itself.
    scope.get_secret = lambda name, default: os.environ.get(name, default)
    assert client_module._default_key_reader() == "env-primary"

    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    assert client_module._default_key_reader() == "env-legacy"
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    assert client_module._default_key_reader() == ""

    # A routed profile with no key in scope must not inherit the launch environment.
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "env-primary")
    scope.get_secret = lambda name, default: default
    assert client_module._default_key_reader() == ""


def test_credential_present_never_returns_a_secret():
    def failing_reader():
        raise RuntimeError("private detail")

    assert client_module.credential_present(lambda: " private-secret ") is True
    assert client_module.credential_present(lambda: "  ") is False
    assert client_module.credential_present(failing_reader) is False


@pytest.mark.parametrize("status", [301, 302, 403, 401])
def test_http_status_errors_fail_open_without_retry(status):
    response = FakeResponse(score_response(), status=status)
    transport = FakeTransport(response)
    assert make_client(transport=transport).classify_detail("task") == (None, "http_error")
    assert len(transport.calls) == 1
    assert response.closed is True


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_transient_http_status_is_retried_once_then_fails_open(status, monkeypatch):
    monkeypatch.setattr(scorer_retry.time, "sleep", lambda _seconds: None)
    transport = FakeTransport(FakeResponse(score_response(), status=status))
    assert make_client(transport=transport).classify_detail("task") == (None, "http_error")
    assert len(transport.calls) == 2


def test_transport_failures_fail_open_without_retry():
    http_error = urllib.error.HTTPError(
        client_module.DEFAULT_ENDPOINT, 401, "unauthorized", {}, io.BytesIO(b"private body"))
    for error, expected in ((http_error, "http_error"),
                            (TimeoutError("private timeout detail"), "timeout"),
                            (urllib.error.URLError(TimeoutError("private detail")), "timeout"),
                            (OSError("private network detail"), "transport_error"),
                            (RuntimeError("private provider detail"), "unexpected_error")):
        transport = FakeTransport(error=error)
        assert make_client(transport=transport).classify_detail("task") == (None, expected)
        assert len(transport.calls) == 1


@pytest.mark.parametrize("payload", [
    None,
    {},
    {"answers": []},
    {"answers": {"effort": {"type": "refusal"}}},
    {"answers": {"other": {"score": 1}}},
    {"answers": {"effort": {"type": "choice", "choice": "low"}}},
    {"answers": {"effort": {"type": "score", "score": True}}},
    {"answers": {"effort": {"type": "score", "score": "1"}}},
    {"answers": {"effort": {"type": "score", "score": float("inf")}}},
    {"answers": {"effort": {"type": "score", "score": -0.01}}},
    {"answers": {"effort": {"type": "score", "score": 2.01}}},
])
def test_refused_malformed_and_invalid_answers_fail_open(payload):
    assert make_client(transport=FakeTransport(payload)).classify_detail("task") == (
        None, "malformed_response")


def test_invalid_json_fails_open_as_malformed_response():
    transport = FakeTransport(FakeResponse(b"not json"))
    assert make_client(transport=transport).classify_detail("task") == (
        None, "malformed_response")


def test_logs_do_not_include_prompt_credential_response_or_exception_details(caplog):
    caplog.set_level(logging.DEBUG)
    prompt = "private task text"
    key = "private-go-key"
    detail = "private provider detail"
    transport = FakeTransport(error=urllib.error.HTTPError(
        client_module.DEFAULT_ENDPOINT, 500, detail, {}, io.BytesIO(detail.encode())))
    client = client_module.OpenCodeGoClient(api_key=key, transport=transport)

    assert client.classify_detail(prompt) == (None, "http_error")
    assert prompt not in caplog.text
    assert key not in caplog.text
    assert detail not in caplog.text
