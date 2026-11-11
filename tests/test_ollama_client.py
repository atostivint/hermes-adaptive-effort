"""Local Ollama adapter contract; transports are faked to keep tests network-free."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from conftest import import_plugin

ollama_client = import_plugin("ollama_client")
scorers = import_plugin("scorers")
middleware = import_plugin("middleware")

PROMPT_MARKER = "private-ollama-prompt-marker"
ENDPOINT = "http://127.0.0.1:11434"


class Response(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def answer(*entries):
    """One chat completion whose first token carries the requested top-logprob list."""
    tops = [{"token": token, "logprob": logprob} for token, logprob in entries]
    top = tops[0] if tops else {"token": "", "logprob": 0.0}
    return {"model": "llama3.2", "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": top["token"]},
        "logprobs": {"content": [{
            "token": top["token"], "logprob": top["logprob"], "top_logprobs": tops}]},
        "finish_reason": "length",
    }]}


class Transport:
    def __init__(self, payload=None, error=None, status=200):
        self.payload = answer(("A", -0.1)) if payload is None else payload
        self.error = error
        self.status = status
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return Response(self.payload, self.status)


def make_client(transport, **kwargs):
    kwargs.setdefault("model", "llama3.2")
    kwargs.setdefault("endpoint", ENDPOINT)
    return ollama_client.OllamaClient(transport=transport, **kwargs)


def http_error(code):
    return urllib.error.HTTPError(ENDPOINT, code, "error", {}, None)


def test_request_asks_for_one_token_and_carries_no_authorization_header():
    transport = Transport(answer(("1", -0.2)))
    client = make_client(transport, timeout=2.5, max_prompt_chars=40)
    assert client.classify_detail(PROMPT_MARKER * 10) == (1.0, None)
    request, timeout = transport.calls[0]
    assert request.full_url == ENDPOINT + "/v1/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") is None
    body = json.loads(request.data)
    assert body["model"] == "llama3.2"
    assert body["max_tokens"] == 1
    assert body["temperature"] == 0
    assert body["logprobs"] is True
    assert body["top_logprobs"] == 5
    assert body["stream"] is False
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert len(body["messages"][1]["content"]) <= 40
    assert timeout == 2.5


def test_named_choices_are_offered_as_letters_and_the_answer_maps_back():
    transport = Transport(answer(("B", -0.05), ("A", -3.0)))
    client = make_client(transport)
    assert client.classify_effort_detail(PROMPT_MARKER, ("low", "high")) == ("high", None)
    system = json.loads(transport.calls[0][0].data)["messages"][0]["content"]
    assert "A = low, B = high" in system


def test_numeric_contract_maps_one_digit_to_the_shared_score():
    transport = Transport(answer(("2", -0.01)))
    client = make_client(transport)
    assert client.classify_detail(PROMPT_MARKER) == (2.0, None)
    assert client.classify(PROMPT_MARKER) == 2.0
    system = json.loads(transport.calls[0][0].data)["messages"][0]["content"]
    assert "0 = low, 1 = medium, 2 = high" in system


def test_highest_logprob_wins_among_several_allowed_letters():
    payload = answer(("A", -1.2), ("B", -0.05), ("C", -3.0))
    assert make_client(Transport(payload)).classify_effort_detail(
        PROMPT_MARKER, ("low", "medium", "high")) == ("medium", None)


def test_only_letters_outside_the_offered_set_are_rejected():
    payload = answer(("C", -0.01), ("D", -2.0))
    assert make_client(Transport(payload)).classify_effort_detail(
        PROMPT_MARKER, ("low", "high")) == (None, "malformed_response")


def test_a_digit_outside_the_contract_is_rejected():
    payload = answer(("3", -0.01))
    assert make_client(Transport(payload)).classify_detail(PROMPT_MARKER) == (
        None, "malformed_response")


def test_a_tie_between_two_different_answers_is_rejected():
    payload = answer(("A", -0.5), ("B", -0.5))
    assert make_client(Transport(payload)).classify_effort_detail(
        PROMPT_MARKER, ("low", "high")) == (None, "malformed_response")


@pytest.mark.parametrize("payload", [
    {"choices": [{"logprobs": {"content": [{"top_logprobs": []}]}}]},
    {"choices": [{"logprobs": {"content": [{}]}}]},
    {"choices": [{"message": {"content": "A"}}]},
    {"choices": []},
    {},
    "not-a-dict",
])
def test_missing_or_empty_logprobs_fail_open(payload):
    assert make_client(Transport(payload)).classify_detail(PROMPT_MARKER) == (
        None, "malformed_response")


def test_a_non_json_body_is_malformed():
    client = make_client(lambda *_: io.BytesIO(b"<html>gateway</html>"))
    assert client.classify_detail(PROMPT_MARKER) == (None, "malformed_response")


def test_a_timeout_fails_open_without_a_retry():
    transport = Transport(error=TimeoutError())
    assert make_client(transport).classify_detail(PROMPT_MARKER) == (None, "timeout")
    assert len(transport.calls) == 1


def test_a_transient_429_is_retried_once_inside_the_deadline():
    payload = answer(("1", -0.2))
    calls = []
    responses = [http_error(429)]

    def flaky(request, timeout):
        calls.append((request, timeout))
        if responses:
            raise responses.pop()
        return Response(payload)

    assert make_client(flaky, timeout=5.0).classify_detail(PROMPT_MARKER) == (1.0, None)
    assert len(calls) == 2


def test_an_invalid_prompt_never_reaches_the_transport():
    transport = Transport()
    client = make_client(transport)
    assert client.classify_detail("   ") == (None, "invalid_prompt")
    assert client.classify_detail(None) == (None, "invalid_prompt")
    assert transport.calls == []


def test_empty_model_fails_open_as_model_missing():
    transport = Transport()
    assert make_client(transport, model="").classify_detail(PROMPT_MARKER) == (
        None, "model_missing")
    assert transport.calls == []
    settings = middleware._settings()
    settings["scorer_provider"] = "ollama"
    settings["ollama_model"] = ""
    assert scorers.build_client(settings) == (None, "model_missing")


@pytest.mark.parametrize("configured", ["not a url", "localhost:11434", "ftp://host", "http://"])
def test_an_obviously_invalid_endpoint_fails_open(configured):
    assert ollama_client.valid_endpoint(configured) is False
    settings = middleware._settings()
    settings.update({"scorer_provider": "ollama", "ollama_model": "llama3.2",
                     "ollama_endpoint": configured})
    assert scorers.build_client(settings) == (None, "endpoint_invalid")
    assert make_client(Transport(), endpoint=configured).classify_detail(PROMPT_MARKER) == (
        None, "endpoint_invalid")


def test_endpoint_normalization_and_chat_path():
    for configured in ("", None, "  "):
        assert ollama_client.normalize_endpoint(configured) == ollama_client.DEFAULT_ENDPOINT
    for configured in (ENDPOINT, ENDPOINT + "/"):
        assert ollama_client.chat_url(configured) == ENDPOINT + "/v1/chat/completions"
    assert ollama_client.chat_url(ENDPOINT + "/v1/chat/completions") == \
        ENDPOINT + "/v1/chat/completions"
    assert ollama_client.valid_endpoint(ENDPOINT) is True


def test_ollama_needs_no_credential_and_requires_none():
    assert scorers.credential_present("ollama") is True
    assert scorers.credential_required("ollama") is False
    assert scorers.credential_required("ollama", "bearer") is False


def test_status_reports_the_local_endpoint_and_model(monkeypatch):
    values = {"scorer_provider": "ollama", "ollama_model": "qwen3:4b",
              "ollama_endpoint": ENDPOINT}
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: values.get(key, default))
    settings = middleware._settings()
    assert settings["scorer_model_effective"] == "qwen3:4b"
    assert settings["scorer_endpoint"] == ENDPOINT
    assert settings["scorer_endpoint_effective"] == ENDPOINT + "/v1/chat/completions"
    assert settings["timeout_s"] == 10.0


def test_classify_never_raises_even_if_internals_do(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("rubric bug")
    monkeypatch.setattr(ollama_client.rubric, "questions_for", broken)
    assert make_client(Transport()).classify_detail(PROMPT_MARKER) == (
        None, "unexpected_error")
    assert make_client(Transport()).classify(PROMPT_MARKER) is None
