"""Custom scorer request formats, validation, and fail-open contract."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tests.conftest import import_plugin

custom_module = import_plugin("custom_client")


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append({"url": request.full_url, "headers": dict(request.header_items()),
                           "body": request.data, "timeout": timeout})
        if self.error:
            raise self.error
        return self.payload


def make_client(**kwargs):
    kwargs.setdefault("endpoint", "https://scorer.example/v1/score")
    kwargs.setdefault("model", "rubric-v1")
    kwargs.setdefault("transport", FakeTransport({"answers": {"effort": {"score": 1.25}}}))
    return custom_module.CustomClient(**kwargs)


def test_systemone_sends_shared_questions_and_reads_unwrapped_answer():
    transport = FakeTransport({"answers": {"effort": {"score": 1.25}}})
    client = make_client(transport=transport, api_format="systemone", auth="none",
                         timeout=2.5, max_prompt_chars=64)
    assert client.classify_detail("prompt") == (pytest.approx(1.25), None)
    call = transport.calls[0]
    body = json.loads(call["body"])
    assert call["url"] == "https://scorer.example/v1/score"
    assert call["timeout"] == pytest.approx(2.5)
    assert "Authorization" not in call["headers"]
    assert body["state"] == {"prompt": "prompt"}
    assert body["model"] == "rubric-v1"
    assert body["questions"] == custom_module.QUESTIONS


def test_systemone_does_not_accept_cloudflare_wrapper():
    transport = FakeTransport({"success": True,
                               "result": {"answers": {"effort": {"score": 1}}}})
    assert make_client(transport=transport).classify_detail("prompt") == (
        None, "malformed_response")


def test_chat_completions_reuses_openrouter_body_without_zdr_options():
    payload = {"choices": [{"message": {"content": '{"score": 0.75}'}}]}
    transport = FakeTransport(payload)
    client = make_client(transport=transport, api_format="chat_completions")
    assert client.classify("prompt") == pytest.approx(0.75)
    body = json.loads(transport.calls[0]["body"])
    assert body["model"] == "rubric-v1"
    assert body["messages"][1]["content"] == "prompt"
    assert body["messages"][0]["content"] == custom_module.rubric.CHAT_SYSTEM_PROMPT
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 32
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert "provider" not in body


def test_bearer_auth_reads_optional_key_and_sends_it():
    transport = FakeTransport({"answers": {"effort": {"score": 1}}})
    reads = []
    client = make_client(transport=transport, auth="bearer",
                         key_reader=lambda: reads.append(True) or "custom-secret")
    assert client.classify("prompt") == 1.0
    assert reads == [True]
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer custom-secret"


def test_auth_none_never_reads_or_sends_a_key():
    transport = FakeTransport({"answers": {"effort": {"score": 1}}})
    client = make_client(transport=transport, auth="none",
                         key_reader=lambda: pytest.fail("key reader must not run"))
    assert client.classify("prompt") == 1.0
    assert "Authorization" not in transport.calls[0]["headers"]
    assert custom_module.credential_present("none", lambda: pytest.fail("must not read"))


def test_missing_bearer_key_does_not_open_transport():
    transport = FakeTransport({})
    client = make_client(transport=transport, auth="bearer", key_reader=lambda: "")
    assert client.classify_detail("prompt") == (None, "credential_missing")
    assert transport.calls == []


@pytest.mark.parametrize(("endpoint", "failure"), [
    ("", "endpoint_missing"),
    ("ftp://scorer.example/path", "endpoint_invalid"),
    ("https:///path", "endpoint_invalid"),
    ("https://user:pass@scorer.example/path", "endpoint_invalid"),
    ("https://scorer.example/path#fragment", "endpoint_invalid"),
    ("https://scorer.example:bad/path", "endpoint_invalid"),
])
def test_endpoint_configuration_fails_before_transport(endpoint, failure):
    transport = FakeTransport({})
    assert make_client(endpoint=endpoint, transport=transport).classify_detail("prompt") == (
        None, failure)
    assert transport.calls == []


def test_query_parameters_are_preserved_for_request_but_redacted_for_display():
    endpoint = "https://scorer.example/path?tenant=public&token=private&empty="
    assert custom_module.safe_endpoint_display(endpoint) == (
        "https://scorer.example/path?tenant=%5Bredacted%5D&token=%5Bredacted%5D"
        "&empty=%5Bredacted%5D")
    transport = FakeTransport({"answers": {"effort": {"score": 1}}})
    assert make_client(endpoint=endpoint, transport=transport).classify("prompt") == 1.0
    assert transport.calls[0]["url"] == endpoint


@pytest.mark.parametrize("endpoint", [
    "https://user:secret@scorer.example/path?token=private",
    "https://scorer.example/path#private-token",
])
def test_unsafe_url_components_are_hidden_from_status(endpoint):
    assert custom_module.safe_endpoint_display(endpoint) == "[invalid endpoint]"


def test_default_transport_redirect_handler_refuses_redirects():
    handler = custom_module._NoRedirectHandler()
    request = urllib.request.Request("https://scorer.example")
    assert handler.redirect_request(request, None, 302, "Found", {},
                                    "https://other.example") is None


@pytest.mark.parametrize(("kwargs", "failure"), [
    ({"api_format": "unknown"}, "unsupported_api_format"),
    ({"auth": "basic"}, "unsupported_auth"),
    ({"model": ""}, "model_missing"),
])
def test_invalid_selection_and_missing_model_fail_before_transport(kwargs, failure):
    transport = FakeTransport({})
    assert make_client(transport=transport, **kwargs).classify_detail("prompt") == (
        None, failure)
    assert transport.calls == []


@pytest.mark.parametrize(("error", "failure"), [
    (TimeoutError("timeout"), "timeout"),
    (ConnectionError("offline"), "transport_error"),
    (ValueError("bad transport"), "unexpected_error"),
])
def test_transport_failures_are_fail_open(error, failure):
    assert make_client(transport=FakeTransport(error=error)).classify_detail("secret prompt") == (
        None, failure)


def test_http_errors_and_malformed_scores_are_fail_open_without_body_logging(caplog):
    error = urllib.error.HTTPError("https://scorer.example", 500, "private-body", {}, None)
    assert make_client(transport=FakeTransport(error=error)).classify_detail("private prompt") == (
        None, "http_error")
    assert make_client(transport=FakeTransport({"answers": {"effort": {"score": True}}})
                       ).classify_detail("private prompt") == (None, "malformed_response")
    assert "private-body" not in caplog.text
    assert "private prompt" not in caplog.text


def test_prompt_is_truncated_before_both_formats_are_sent():
    for api_format in custom_module.API_FORMATS:
        transport = FakeTransport({"answers": {"effort": {"score": 1}}})
        client = make_client(transport=transport, api_format=api_format, max_prompt_chars=64)
        client.classify("x" * 500)
        body = json.loads(transport.calls[0]["body"])
        sent = (body["state"]["prompt"] if api_format == "systemone"
                else body["messages"][1]["content"])
        assert len(sent) <= 64
