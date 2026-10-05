"""Cloudflare Clef adapter contract; transports are faked to keep tests network-free."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from tests.conftest import import_plugin

client_module = import_plugin("cloudflare_client")

ACCOUNT = "0123456789abcdef0123456789abcdef"


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def wrapper(score=1.25, **extra):
    return {"success": True, "errors": [], "messages": [], "result": {
        "answers": {"effort": {"score": score}}}, **extra}


def make_client(**kwargs):
    return client_module.CloudflareClient(
        account_id=ACCOUNT, api_key="token", transport=lambda *_: wrapper(), **kwargs)


def test_request_uses_fixed_account_route_and_shared_bounded_rubric():
    seen = {}

    def transport(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return wrapper()

    client = client_module.CloudflareClient(account_id=ACCOUNT, api_key="token",
        timeout=2.5, max_prompt_chars=40, transport=transport)
    assert client.classify_detail("x" * 200) == (1.25, None)
    request = seen["request"]
    assert request.full_url == f"{client_module.API_ROOT}/{ACCOUNT}/ai/run/@cf/cloudflare/clef"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer token"
    body = json.loads(request.data)
    assert body["model"] == "clef"
    assert len(body["state"]["prompt"]) <= 40
    assert body["questions"] == client_module.QUESTIONS
    assert seen["timeout"] == 2.5


def test_context_managed_response_is_decoded():
    client = client_module.CloudflareClient(account_id=ACCOUNT, api_key="token",
        transport=lambda *_: Response(json.dumps(wrapper(0.5)).encode()))
    assert client.classify_detail("prompt") == (0.5, None)


@pytest.mark.parametrize("score", [True, "1", -0.1, 2.1, float("nan"), float("inf"), 10**1000])
def test_invalid_score_types_and_ranges_fail_open(score):
    assert client_module._extract_score(wrapper(score)) is None


@pytest.mark.parametrize("payload", [
    {}, {"success": False, "result": {"answers": {"effort": {"score": 1}}}},
    {"success": True, "errors": [{"code": 1000}], "result": {"answers": {"effort": {"score": 1}}}},
    {"success": True, "result": {"answers": {"effort": {"score": None}}}},
])
def test_incomplete_or_error_wrappers_fail_open(payload):
    assert client_module._extract_score(payload) is None


@pytest.mark.parametrize(("transport", "failure"), [
    (lambda *_: (_ for _ in ()).throw(urllib.error.HTTPError("url", 403, "no", {}, None)), "http_error"),
    (lambda *_: (_ for _ in ()).throw(TimeoutError()), "timeout"),
])
def test_http_and_timeout_errors_fail_open(transport, failure):
    client = client_module.CloudflareClient(account_id=ACCOUNT, api_key="token", transport=transport)
    assert client.classify_detail("prompt") == (None, failure)


def test_invalid_account_is_rejected_before_transport_and_empty_has_missing_code():
    calls = []
    bad = client_module.CloudflareClient(account_id="bad/path", api_key="token",
        transport=lambda *_: calls.append("called"))
    assert bad.classify_detail("prompt") == (None, "account_invalid")
    missing = client_module.CloudflareClient(account_id="", api_key="token",
        transport=lambda *_: calls.append("called"))
    assert missing.classify_detail("prompt") == (None, "account_missing")
    assert calls == []
    assert "/bad/path/" not in bad.endpoint


def test_missing_token_and_invalid_prompt_skip_transport():
    calls = []
    client = client_module.CloudflareClient(account_id=ACCOUNT,
        key_reader=lambda: "", transport=lambda *_: calls.append("called"))
    assert client.classify_detail("prompt") == (None, "credential_missing")
    assert client.classify_detail(" ") == (None, "invalid_prompt")
    assert calls == []


def test_cloudflare_secret_scope_precedes_environment_then_falls_back(monkeypatch):
    import sys
    import types

    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = lambda name, default: "scope-token"
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    monkeypatch.setenv("CLOUDFLARE_AUTH_TOKEN", "env-token")
    assert client_module._default_key_reader() == "scope-token"

    scope.get_secret = lambda *_: ""
    assert client_module._default_key_reader() == "env-token"
    assert client_module.credential_present(lambda: " token ") is True
    assert client_module.credential_present(lambda: "") is False


def test_prompt_and_token_never_appear_in_logs(caplog):
    marker = "private-cloudflare-prompt-marker"
    secret = "private-cloudflare-secret-marker"
    client = client_module.CloudflareClient(account_id=ACCOUNT, api_key=secret,
        transport=lambda *_: wrapper())
    with caplog.at_level("DEBUG"):
        assert client.classify_detail(marker)[0] == 1.25
    assert marker not in caplog.text
    assert secret not in caplog.text
