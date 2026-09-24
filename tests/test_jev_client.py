"""Jev classification adapter: bounded, validated, fail-open. Fake transport only."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

jev_client = import_plugin("jev_client")


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTransport:
    """Stands in for urllib.request.urlopen; never touches the network."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append({"url": request.full_url, "timeout": timeout,
                           "headers": dict(request.header_items()),
                           "body": request.data})
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload)


def _answer(score, extra=None):
    answer = {"score": score}
    if extra:
        answer.update(extra)
    return {"answers": {"effort": answer}, "model": "jev-latest", "provider": "typesafe"}


def make_client(**kwargs):
    kwargs.setdefault("transport", FakeTransport(_answer(1.0)))
    kwargs.setdefault("api_key", "test-key")
    return jev_client.JevClient(**kwargs)


def test_valid_scores_return_float():
    for score in (0.0, 0.4, 0.5, 1.0, 1.9, 2.0):
        client = jev_client.JevClient(api_key="k", transport=FakeTransport(_answer(score)))
        assert client.classify("prompt") == pytest.approx(score)


def test_malformed_responses_return_none():
    cases = [
        {},                                   # no answers
        {"answers": {}},                      # question unanswered
        {"answers": {"effort": "high"}},      # not an answer object
        {"answers": {"effort": {"score": "1"}}},   # not numeric
        {"answers": {"effort": {"score": True}}},  # bool is not a score
        {"answers": {"effort": {"score": -0.5}}},  # outside rubric range
        {"answers": {"effort": {"score": 3.0}}},   # outside rubric range
        {"answers": {"effort": {"score": float("nan")}}},
        "not-a-dict",
    ]
    for payload in cases:
        client = jev_client.JevClient(api_key="k", transport=FakeTransport(payload))
        assert client.classify("prompt") is None, payload


def test_timeout_returns_none():
    client = jev_client.JevClient(
        api_key="k", transport=FakeTransport(error=TimeoutError("timed out")))
    assert client.classify("prompt") is None


def test_transport_exception_returns_none():
    for error in (ConnectionError("boom"), OSError("dns"), ValueError("bad json")):
        client = jev_client.JevClient(api_key="k", transport=FakeTransport(error=error))
        assert client.classify("prompt") is None, error


def test_missing_key_never_builds_a_request():
    transport = FakeTransport(_answer(1.0))
    client = jev_client.JevClient(api_key="", transport=transport, key_reader=lambda: "")
    assert client.classify("prompt") is None
    assert transport.calls == []


def test_timeout_is_passed_to_transport():
    transport = FakeTransport(_answer(1.0))
    client = jev_client.JevClient(api_key="k", transport=transport, timeout=2.5)
    client.classify("prompt")
    assert transport.calls[0]["timeout"] == pytest.approx(2.5)


def test_request_shape_matches_verified_typesafe_contract():
    transport = FakeTransport(_answer(1.0))
    client = jev_client.JevClient(api_key="secret-key", transport=transport)
    client.classify("the prompt")
    call = transport.calls[0]
    assert call["url"] == jev_client.DEFAULT_ENDPOINT
    assert call["headers"].get("Authorization") == "Bearer secret-key"
    body = json.loads(call["body"])
    assert body["model"] == jev_client.JEV_MODEL
    assert body["state"]["prompt"] == "the prompt"
    assert set(body["questions"]) == {"effort"}
    assert body["questions"]["effort"]["type"] == "score"
    assert len(body["questions"]["effort"]["criteria"]) == 3


def test_prompt_is_truncated_before_send():
    transport = FakeTransport(_answer(1.0))
    client = jev_client.JevClient(api_key="k", transport=transport, max_prompt_chars=64)
    client.classify("x" * 500)
    body = json.loads(transport.calls[0]["body"])
    assert len(body["state"]["prompt"]) <= 64
