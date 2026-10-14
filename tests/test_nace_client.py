"""Nace Drex adapter contract; transports are faked to keep tests network-free."""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.error

import pytest

from conftest import import_plugin

nace_client = import_plugin("nace_client")

PROMPT_MARKER = "private-nace-prompt-marker"
SECRET = "private-nace-secret-marker"


class Response(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def answer(score=1.25, model="drex-v2.0"):
    """Documented Drex response shape; ``confidence`` is deliberately not the score."""
    return {"model": model, "answers": {"effort": {
        "type": "score", "score": score, "legend": ["Low", "Medium", "High"],
        "probabilities": {"0": 0.1, "1": 0.55, "2": 0.35}, "confidence": 0.42}},
        "usage": {"input_tokens": 120, "output_tokens": 1},
        "evaluation_time_ms": 80, "request_id": "req_test"}


class Transport:
    def __init__(self, payload=None, error=None, status=200):
        self.payload = answer() if payload is None else payload
        self.error = error
        self.status = status
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return Response(self.payload, self.status)


def make_client(transport, **kwargs):
    kwargs.setdefault("api_key", "test-key")
    return nace_client.NaceClient(transport=transport, **kwargs)


def http_error(code):
    return urllib.error.HTTPError(nace_client.DEFAULT_ENDPOINT, code, "error", {}, None)


def test_happy_path_returns_the_score_not_the_confidence():
    transport = Transport(answer(1.25))
    client = make_client(transport, timeout=2.5, max_prompt_chars=40)
    assert client.classify_detail("x" * 200) == (1.25, None)
    assert client.classify("prompt") == 1.25
    request, timeout = transport.calls[0]
    assert request.full_url == "https://console.nace.ai/v1/systemone"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert request.get_header("Content-type") == "application/json"
    body = json.loads(request.data)
    assert body["model"] == "drex-v2.0"
    assert len(body["state"]["prompt"]) <= 40
    assert body["questions"] == nace_client.QUESTIONS
    assert timeout == 2.5


def test_configured_model_is_sent_and_a_downgraded_response_model_is_still_accepted():
    transport = Transport(answer(0.2, model="drex-v1.5"))
    client = make_client(transport, model="drex-latest")
    assert client.classify_detail("prompt") == (0.2, None)
    assert json.loads(transport.calls[0][0].data)["model"] == "drex-latest"


@pytest.mark.parametrize("payload", [
    {}, {"answers": {}}, {"answers": {"effort": "high"}},
    {"answers": {"effort": {"confidence": 0.9}}},
    {"answers": {"effort": {"score": "1"}}}, {"answers": {"effort": {"score": True}}},
    {"answers": {"effort": {"score": -0.1}}}, {"answers": {"effort": {"score": 2.1}}},
    {"answers": {"effort": {"score": float("nan")}}}, "not-a-dict", [1, 2],
])
def test_malformed_bodies_fail_open(payload):
    assert make_client(Transport(payload)).classify_detail("prompt") == (
        None, "malformed_response")


def test_non_json_body_is_malformed():
    client = make_client(lambda *_: io.BytesIO(b"<html>gateway</html>"))
    assert client.classify_detail("prompt") == (None, "malformed_response")


@pytest.mark.parametrize("code", [401, 402, 422, 429, 500, 529])
def test_documented_error_statuses_fail_open_as_http_error(code):
    assert make_client(Transport(error=http_error(code))).classify_detail("prompt") == (
        None, "http_error")


def test_non_2xx_status_on_a_returned_response_is_http_error():
    assert make_client(Transport(answer(), status=503)).classify_detail("prompt") == (
        None, "http_error")


@pytest.mark.parametrize(("error", "failure"), [
    (TimeoutError(), "timeout"),
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (urllib.error.URLError("dns"), "transport_error"),
    (ConnectionError("reset"), "transport_error"),
    (ValueError("bad"), "unexpected_error"),
])
def test_transport_failures_fail_open(error, failure):
    assert make_client(Transport(error=error)).classify_detail("prompt") == (None, failure)


def test_missing_credential_and_invalid_prompt_never_reach_the_transport():
    transport = Transport()
    client = nace_client.NaceClient(transport=transport, key_reader=lambda: "")
    assert client.classify_detail("prompt") == (None, "credential_missing")
    assert make_client(transport).classify_detail("   ") == (None, "invalid_prompt")
    assert make_client(transport).classify_detail(None) == (None, "invalid_prompt")
    assert transport.calls == []


def test_raising_key_reader_is_treated_as_missing_credential():
    def boom():
        raise RuntimeError("secret store down")
    client = nace_client.NaceClient(transport=Transport(), key_reader=boom)
    assert client.classify_detail("prompt") == (None, "credential_missing")
    assert nace_client.credential_present(boom) is False


def test_nace_key_wins_over_legacy_drex_key_and_secret_scope_precedes_env(monkeypatch):
    # Never consult the real Hermes secret store from a unit test.
    empty_scope = types.ModuleType("agent.secret_scope")
    empty_scope.get_secret = lambda name, default: default
    monkeypatch.setitem(sys.modules, "agent.secret_scope", empty_scope)
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    monkeypatch.delenv("DREX_API_KEY", raising=False)
    assert nace_client._default_key_reader() == ""
    assert nace_client.credential_present() is False

    monkeypatch.setenv("DREX_API_KEY", "legacy-env")
    assert nace_client._default_key_reader() == "legacy-env"
    monkeypatch.setenv("NACE_API_KEY", "nace-env")
    assert nace_client._default_key_reader() == "nace-env"

    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = lambda name, default: {"NACE_API_KEY": "nace-scope"}.get(name, default)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    assert nace_client._default_key_reader() == "nace-scope"
    assert nace_client.credential_present() is True


def test_base_url_normalization(monkeypatch):
    for configured in ("https://console.nace.ai", "https://console.nace.ai/",
                       "https://console.nace.ai/v1", "  https://console.nace.ai/v1/  ",
                       "", None):
        assert nace_client.normalize_endpoint(configured) == nace_client.DEFAULT_ENDPOINT
    for verbatim in ("https://console.nace.ai/v1/systemone", "https://proxy.internal/team/drex"):
        assert nace_client.normalize_endpoint(verbatim) == verbatim

    monkeypatch.delenv("NACE_BASE_URL", raising=False)
    assert nace_client.endpoint_for() == nace_client.DEFAULT_ENDPOINT
    monkeypatch.setenv("NACE_BASE_URL", "http://127.0.0.1:9000")
    assert nace_client.configured_base_url() == "http://127.0.0.1:9000"
    assert nace_client.endpoint_for() == "http://127.0.0.1:9000/v1/systemone"
    transport = Transport()
    make_client(transport, endpoint=nace_client.configured_base_url()).classify("prompt")
    assert transport.calls[0][0].full_url == "http://127.0.0.1:9000/v1/systemone"


@pytest.mark.parametrize("transport", [
    Transport(answer(1.0)), Transport({}), Transport(error=http_error(401)),
    Transport(error=TimeoutError()), Transport(error=ValueError(PROMPT_MARKER)),
])
def test_prompt_and_key_never_reach_reason_codes_or_logs(transport, caplog):
    client = make_client(transport, api_key=SECRET)
    with caplog.at_level("DEBUG"):
        score, failure = client.classify_detail(PROMPT_MARKER)
    for text in (repr(score), repr(failure), caplog.text):
        if PROMPT_MARKER in text or SECRET in text:
            raise AssertionError("prompt or key leaked into a reason code or log line")


def test_classify_never_raises_even_if_internals_do(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("rubric bug")
    monkeypatch.setattr(nace_client.rubric, "questions_for", broken)
    client = make_client(Transport())
    assert client.classify_detail("prompt") == (None, "unexpected_error")
    assert client.classify("prompt") is None
