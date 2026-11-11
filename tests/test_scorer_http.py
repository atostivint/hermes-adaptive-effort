"""Shared scorer transport: redirect refusal, response closing, key cache (no network)."""

from __future__ import annotations

import io
import os
import urllib.error
import urllib.request
import urllib.response

import pytest

from conftest import PLUGIN_DIR, import_plugin

scorer_http = import_plugin("scorer_http")
jev_client = import_plugin("jev_client")
openrouter_client = import_plugin("openrouter_client")
openai_client = import_plugin("openai_decision_client")
cloudflare_client = import_plugin("cloudflare_client")
nace_client = import_plugin("nace_client")
custom_client = import_plugin("custom_client")

OK_BODY = b'{"answers": {"effort": {"score": 1.0}}}'

ADAPTERS = {
    "jev": (jev_client, lambda t, **kw: jev_client.JevClient(transport=t, **kw)),
    "openrouter": (openrouter_client, lambda t, **kw: openrouter_client.OpenRouterClient(
        model="m", transport=t, **kw)),
    "openai": (openai_client, lambda t, **kw: openai_client.OpenAIDecisionClient(
        transport=t, **kw)),
    "cloudflare": (cloudflare_client, lambda t, **kw: cloudflare_client.CloudflareClient(
        account_id="a" * 32, transport=t, **kw)),
    "nace": (nace_client, lambda t, **kw: nace_client.NaceClient(transport=t, **kw)),
    "custom": (custom_client, lambda t, **kw: custom_client.CustomClient(
        endpoint="https://scorer.example/s", model="m", transport=t, **kw)),
}
NAMES = sorted(ADAPTERS)


class Response:
    def __init__(self, body=OK_BODY, status=200):
        self.body, self.status, self.closed = body, status, False

    def read(self):
        return self.body

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class PlainResponse:
    """No context-manager protocol: the shared reader must close it itself."""

    status = 200

    def __init__(self):
        self.closed = False

    def read(self):
        return OK_BODY

    def close(self):
        self.closed = True


class ClosableBody(io.BytesIO):
    pass


def http_error(code=500):
    fp = ClosableBody(b"{}")
    return urllib.error.HTTPError("https://scorer.example/s", code, "x", {}, fp), fp


# ── redirect refusal ─────────────────────────────────────────────────────────


class _FakeRedirectingHTTP(urllib.request.BaseHandler):
    """Answers every http request with a 302 so no socket is ever opened."""

    def __init__(self):
        self.seen = []

    def http_open(self, req):
        self.seen.append(req.full_url)
        hdrs = {"Location": "http://other.example/steal"}
        resp = urllib.response.addinfourl(io.BytesIO(b""), hdrs, req.full_url, 302)
        resp.msg = "Found"
        return resp


def _opener_with(handler_for_http):
    # Same stack as default_transport's opener, with the socket layer faked.
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPErrorProcessor(),
                    scorer_http.NoRedirectHandler(), handler_for_http):
        opener.add_handler(handler)
    return opener


def test_no_redirect_handler_refuses_redirects():
    request = urllib.request.Request("http://scorer.example/s")
    assert scorer_http.NoRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "http://other.example") is None


def test_redirect_surfaces_as_http_error_and_is_never_followed():
    fake = _FakeRedirectingHTTP()
    opener = _opener_with(fake)
    request = urllib.request.Request("http://scorer.example/s", headers={
        "Authorization": "Bearer secret"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        opener.open(request, timeout=1)
    assert caught.value.code == 302
    assert fake.seen == ["http://scorer.example/s"]


def test_default_transport_opener_contains_the_no_redirect_handler(monkeypatch):
    monkeypatch.setattr(scorer_http, "_OPENER", None)
    built = []
    real = urllib.request.build_opener

    def spy(*handlers):
        built.append(handlers)
        return real(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", spy)
    opener = scorer_http._get_opener()
    assert any(isinstance(h, scorer_http.NoRedirectHandler) for h in built[0])
    assert any(isinstance(h, scorer_http.NoRedirectHandler) for h in opener.handlers)
    assert scorer_http._get_opener() is opener


@pytest.mark.parametrize("name", NAMES)
def test_every_adapter_default_transport_uses_the_shared_no_redirect_opener(name, monkeypatch):
    module, _ = ADAPTERS[name]
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return "response"

    monkeypatch.setattr(scorer_http, "_OPENER", Opener())
    request = urllib.request.Request("https://scorer.example/s")
    assert module._default_transport(request, 1.5) == "response"
    assert calls == [(request, 1.5)]


# ── response closing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_http_error_response_is_closed(name):
    _, make = ADAPTERS[name]
    error, fp = http_error(401)

    def transport(request, timeout):
        raise error

    assert make(transport, api_key="k").classify_detail("prompt") == (None, "http_error")
    assert fp.closed


@pytest.mark.parametrize("name", NAMES)
def test_non_2xx_response_is_closed(name):
    _, make = ADAPTERS[name]
    response = Response(status=302)
    assert make(lambda r, t: response, api_key="k").classify_detail("prompt") == (
        None, "http_error")
    assert response.closed


@pytest.mark.parametrize("name", NAMES)
def test_successful_response_is_closed(name):
    _, make = ADAPTERS[name]
    response = Response()
    make(lambda r, t: response, api_key="k").classify_detail("prompt")
    assert response.closed


@pytest.mark.parametrize("name", NAMES)
def test_unparseable_response_is_closed_and_malformed(name):
    _, make = ADAPTERS[name]
    response = Response(body=b"not json")
    assert make(lambda r, t: response, api_key="k").classify_detail("prompt") == (
        None, "malformed_response")
    assert response.closed


def test_read_json_closes_a_response_without_context_manager_support():
    response = PlainResponse()
    assert scorer_http.read_json(response) == ({"answers": {"effort": {"score": 1.0}}}, None)
    assert response.closed


@pytest.mark.parametrize(("exc", "reason"), [
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (urllib.error.URLError("dns"), "transport_error"),
    (TimeoutError(), "timeout"),
    (ConnectionResetError(), "transport_error"),
    (RuntimeError("boom"), "unexpected_error"),
])
def test_send_reason_ladder(exc, reason):
    def transport(request, timeout):
        raise exc

    request = scorer_http.build_request("https://scorer.example/s", {}, user_agent="ua")
    assert scorer_http.send(request, transport, 1.0) == (None, reason)


def test_http_error_status_code_is_logged_at_info(caplog):
    def refused(request, timeout):
        raise urllib.error.HTTPError("u", 529, "overloaded", {}, io.BytesIO(b""))

    request = scorer_http.build_request("https://scorer.example/s", {}, user_agent="ua")
    with caplog.at_level("INFO", logger=scorer_http.logger.name):
        assert scorer_http.send(request, refused, 1.0) == (None, "http_error")
    assert "HTTP 529" in caplog.text
    assert "overloaded" not in caplog.text


def test_non_2xx_response_status_code_is_logged_at_info(caplog):
    response = Response(status=529)
    with caplog.at_level("INFO", logger=scorer_http.logger.name):
        assert scorer_http.read_json(response) == (None, "http_error")
    assert "HTTP 529" in caplog.text


def test_build_request_adds_authorization_only_with_a_key():
    plain = scorer_http.build_request("https://scorer.example/s", {"a": 1}, user_agent="ua")
    keyed = scorer_http.build_request("https://scorer.example/s", {"a": 1}, key="k",
                                      user_agent="ua")
    assert plain.get_header("Authorization") is None
    assert keyed.get_header("Authorization") == "Bearer k"
    assert keyed.get_method() == "POST"
    assert keyed.get_header("User-agent") == "ua"


def test_build_request_extra_headers_never_replace_the_fixed_headers():
    request = scorer_http.build_request(
        "https://scorer.example/s", {}, key="k", user_agent="ua",
        extra_headers={"X-Client": "id", "User-Agent": "other", "Authorization": "Bearer x",
                       "Content-Type": "text/plain"})
    assert request.get_header("X-client") == "id"
    assert request.get_header("User-agent") == "ua"
    assert request.get_header("Authorization") == "Bearer k"
    assert request.get_header("Content-type") == "application/json"



def test_extra_headers_cannot_override_fixed_headers_in_any_case_or_order():
    request = scorer_http.build_request(
        "https://scorer.example/s", {}, key="k", user_agent="ua",
        extra_headers={"User-Agent": "first", "user-agent": "override", "ACCEPT": "x",
                       "content-TYPE": "text/plain", "authorization": "Bearer z",
                       " Authorization": "Bearer w", "X-Client": "id"})
    names = [name.lower() for name, _ in request.header_items()]
    assert sorted(names) == ["accept", "authorization", "content-type", "user-agent", "x-client"]
    assert request.get_header("User-agent") == "ua"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "Bearer k"


def test_extra_authorization_is_dropped_when_there_is_no_key():
    request = scorer_http.build_request(
        "https://scorer.example/s", {}, user_agent="ua",
        extra_headers={"Authorization": "Bearer x", "AUTHORIZATION": "Bearer y"})
    assert request.get_header("Authorization") is None
    assert all(name.lower() != "authorization" for name, _ in request.header_items())


@pytest.mark.parametrize("extra", [
    {"X-Client": "a\r\nX-Injected: 1"}, {"X-Client": "a\nb"}, {"X-Client": "a\rb"},
    {"X-Client": "a\0b"}, {"X-Client\r\nX-Injected": "1"}, {"User-Agent\n": "x"},
])
def test_extra_headers_with_line_breaks_are_rejected(extra):
    with pytest.raises(ValueError):
        scorer_http.build_request("https://scorer.example/s", {}, user_agent="ua",
                                  extra_headers=extra)

# ── client identity ──────────────────────────────────────────────────────────


def _plugin_yaml_version():
    from ruamel.yaml import YAML
    return YAML(typ="safe").load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))["version"]


def test_user_agent_reports_the_plugin_yaml_version():
    version = _plugin_yaml_version()
    assert scorer_http.PLUGIN_VERSION == version
    assert scorer_http.USER_AGENT == f"hermes-adaptive-effort/{version}"


@pytest.mark.parametrize("text", ["", "name: x\n", "version:\n", "version: 1.0 junk\n"])
def test_unreadable_plugin_version_is_unknown(tmp_path, text):
    path = tmp_path / "plugin.yaml"
    path.write_text(text, encoding="utf-8")
    assert scorer_http._read_plugin_version(path) == "unknown"
    assert scorer_http._read_plugin_version(tmp_path / "missing.yaml") == "unknown"


def test_version_of_up_to_64_characters_is_read_and_longer_is_unknown(tmp_path):
    path = tmp_path / "plugin.yaml"
    path.write_text("name: x\nversion: 1.0.0-" + "a" * 58 + "\n", encoding="utf-8")
    assert scorer_http._read_plugin_version(path) == "1.0.0-" + "a" * 58
    path.write_text("name: x\nversion: 1.0.0-" + "a" * 59 + "\n", encoding="utf-8")
    assert scorer_http._read_plugin_version(path) == "unknown"


def test_crlf_plugin_yaml_still_reports_its_version(tmp_path):
    # A Windows checkout with autocrlf writes CRLF line endings.
    path = tmp_path / "plugin.yaml"
    path.write_bytes(b"name: x\r\nversion: 1.2.3\r\nkind: standalone\r\n")
    assert scorer_http._read_plugin_version(path) == "1.2.3"
    path.write_bytes(b"version: 1.2.3\r\r\n")
    assert scorer_http._read_plugin_version(path) == "unknown"


def test_huge_plugin_yaml_is_read_only_up_to_the_limit(tmp_path):
    limit = scorer_http._VERSION_READ_LIMIT
    path = tmp_path / "plugin.yaml"
    path.write_bytes(b"version: 1.2.3\n" + b"#" * (8 * 1024 * 1024))
    assert scorer_http._read_plugin_version(path) == "1.2.3"
    path.write_bytes(b"#" * limit + b"\nversion: 1.2.3\n")
    assert scorer_http._read_plugin_version(path) == "unknown"
    # A version line cut by the limit is dropped, never matched as a truncated value.
    content = b"#" * (limit - len(b"version: 1.2") - 1) + b"\nversion: 1.2.3\n" + b"#" * 100
    assert content[:limit].endswith(b"\nversion: 1.2")
    path.write_bytes(content)
    assert scorer_http._read_plugin_version(path) == "unknown"


def test_non_regular_plugin_yaml_is_unknown_without_blocking(tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    candidates = [directory]
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        candidates.append(fifo)
    for path in candidates:
        assert scorer_http._read_plugin_version(path) == "unknown", path


@pytest.mark.parametrize("error", [MemoryError(), OSError("io"), UnicodeError("bad")])
def test_any_read_error_is_unknown(tmp_path, monkeypatch, error):
    path = tmp_path / "plugin.yaml"
    path.write_text("version: 1.2.3\n", encoding="utf-8")

    def broken(*_args):
        raise error

    monkeypatch.setattr(scorer_http.os, "read", broken)
    assert scorer_http._read_plugin_version(path) == "unknown"


def test_invalid_utf8_plugin_yaml_is_unknown(tmp_path):
    path = tmp_path / "plugin.yaml"
    path.write_bytes(b"version: 1.2.3\n\xff\xfe")
    assert scorer_http._read_plugin_version(path) == "unknown"


@pytest.mark.parametrize("name", NAMES)
def test_every_adapter_sends_the_current_version_and_only_nace_sends_its_client_header(name):
    module, make = ADAPTERS[name]
    seen = []

    def transport(request, timeout):
        seen.append(request)
        return Response()

    make(transport, api_key="k").classify("prompt")
    request, = seen
    version = _plugin_yaml_version()
    product = "hermes-hermes-adaptive-effort" if name == "jev" else "hermes-adaptive-effort"
    assert request.get_header("User-agent") == f"{product}/{version}"
    if name == "nace":
        assert request.get_header("X-nace-client") == f"hermes-adaptive-effort/{version}"
    else:
        assert request.get_header("X-nace-client") is None


def test_retry_semantics_are_unchanged_through_the_shared_sender():
    calls = []

    def transport(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b""))
        return Response()

    request = scorer_http.build_request("https://scorer.example/s", {}, user_agent="ua")
    payload, failure = scorer_http.send(request, transport, 3.0)
    assert failure is None and payload.status == 200 and len(calls) == 2

    calls.clear()

    def refused(request, timeout):
        calls.append(timeout)
        raise urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b""))

    assert scorer_http.send(request, refused, 3.0) == (None, "http_error")
    assert len(calls) == 1


# ── key cache ────────────────────────────────────────────────────────────────


def test_resolve_key_reuses_a_key_within_the_ttl_and_rereads_after_it():
    now = [100.0]
    reads = []

    def reader():
        reads.append(1)
        return f" key-{len(reads)} "

    clock = lambda: now[0]  # noqa: E731
    assert scorer_http.resolve_key("p", reader, clock=clock) == "key-1"
    now[0] += scorer_http.KEY_TTL_S - 1
    assert scorer_http.resolve_key("p", reader, clock=clock) == "key-1"
    assert len(reads) == 1
    now[0] += 2
    assert scorer_http.resolve_key("p", reader, clock=clock) == "key-2"
    assert len(reads) == 2


def test_resolve_key_is_per_provider_and_never_caches_an_empty_or_failed_read():
    assert scorer_http.resolve_key("a", lambda: "ka") == "ka"
    assert scorer_http.resolve_key("b", lambda: "kb") == "kb"
    assert scorer_http.resolve_key("a", lambda: "other") == "ka"

    assert scorer_http.resolve_key("c", lambda: "") == ""
    assert scorer_http.resolve_key("c", lambda: "late") == "late"

    def broken():
        raise RuntimeError("scope down")

    assert scorer_http.resolve_key("d", broken) == ""
    assert scorer_http.resolve_key("d", lambda: "back") == "back"


def test_clear_key_cache_and_reset_state_drop_cached_keys():
    middleware = import_plugin("middleware")
    scorer_http.resolve_key("p", lambda: "old")
    scorer_http.clear_key_cache()
    assert scorer_http.resolve_key("p", lambda: "new") == "new"
    middleware.reset_state()
    assert scorer_http.resolve_key("p", lambda: "newer") == "newer"


@pytest.mark.parametrize("name", NAMES)
def test_adapter_reads_the_default_key_once_across_calls(name, monkeypatch):
    module, make = ADAPTERS[name]
    reads = []

    def reader():
        reads.append(1)
        return "secret-key"

    monkeypatch.setattr(module, "_default_key_reader", reader)
    seen = []

    def transport(request, timeout):
        seen.append(request.get_header("Authorization"))
        return Response()

    client = make(transport, **({"auth": "bearer"} if name == "custom" else {}))
    client.classify_detail("one")
    client.classify_detail("two")
    assert len(reads) == 1
    assert seen == ["Bearer secret-key"] * 2
    scorer_http.clear_key_cache()
    client.classify_detail("three")
    assert len(reads) == 2


@pytest.mark.parametrize("name", NAMES)
def test_injected_key_reader_is_never_cached(name):
    _, make = ADAPTERS[name]
    keys = iter(["first", "second"])
    seen = []

    def transport(request, timeout):
        seen.append(request.get_header("Authorization"))
        return Response()

    kwargs = {"key_reader": lambda: next(keys)}
    if name == "custom":
        kwargs["auth"] = "bearer"
    client = make(transport, **kwargs)
    client.classify_detail("one")
    client.classify_detail("two")
    assert seen == ["Bearer first", "Bearer second"]


@pytest.mark.parametrize("name", NAMES)
def test_credential_present_is_a_bool_and_ignores_the_cache(name, monkeypatch):
    module, make = ADAPTERS[name]
    scorer_http.resolve_key({"openai": "openai_decision"}.get(name, name), lambda: "cached")
    monkeypatch.setattr(module, "_default_key_reader", lambda: "")
    probe = (module.credential_present("bearer") if name == "custom"
             else module.credential_present())
    assert probe is False
