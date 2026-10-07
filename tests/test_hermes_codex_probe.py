"""Offline request-boundary tests for the one-shot Codex probe."""

import json

import httpx
import pytest

from scripts.hermes_codex_probe import _ProbeGate, _update_request


def _request(model="gpt-6-sol", effort="low", *, url=None, method="POST",
             max_output_tokens=None):
    body = {"model": model, "reasoning": {"effort": effort}, "tools": []}
    if max_output_tokens is not None:
        body["max_output_tokens"] = max_output_tokens
    return httpx.Request(
        method,
        url or "https://chatgpt.com/backend-api/codex/responses",
        json=body,
    )


def _state():
    return {"plugin_ready": True, "transport_attempts": 0, "send_attempts": 0,
            "blocked_non_target": 0}


def test_availability_probe_observes_unmodified_effort_without_generation():
    request = _request(effort="none")
    original = json.loads(request.content)
    state = _state()

    with pytest.raises(_ProbeGate, match="dry_run_blocked_before_send"):
        _update_request(httpx, request, state, "gpt-6-sol", None, False, True)

    assert state["observed_model"] == "gpt-6-sol"
    assert state["effort_field"] == "reasoning.effort"
    assert state["observed_effort"] == "none"
    assert state["send_attempts"] == 0
    assert json.loads(request.content) == original


def test_effort_probe_blocks_a_wire_value_that_does_not_match_target():
    state = _state()

    with pytest.raises(_ProbeGate, match="wire_effort_mismatch"):
        _update_request(httpx, _request(effort="none"), state,
                       "gpt-6-sol", "low", True, False)

    assert state["failure_code"] == "wire_effort_mismatch"
    assert state["send_attempts"] == 0


def test_effort_probe_dry_run_accepts_exact_wire_value_but_never_sends():
    state = _state()

    with pytest.raises(_ProbeGate, match="dry_run_blocked_before_send"):
        _update_request(httpx, _request(effort="low"), state,
                       "gpt-6-sol", "low", False, False)

    assert state["observed_effort"] == "low"
    assert state["effort_field"] == "reasoning.effort"
    assert state["output_token_cap"] == 256
    assert state["send_attempts"] == 0


@pytest.mark.parametrize("url,method", [
    ("http://chatgpt.com/backend-api/codex/responses", "POST"),
    ("https://chatgpt.com:444/backend-api/codex/responses", "POST"),
    ("https://chatgpt.com/backend-api/codex/responses/", "POST"),
    ("https://chatgpt.com/backend-api/codex/responses?x=1", "POST"),
    ("https://chatgpt.com/backend-api/codex/responses", "GET"),
])
def test_effort_probe_rejects_non_exact_endpoint(url, method):
    state = _state()

    with pytest.raises(_ProbeGate, match="non_target_transport_blocked"):
        _update_request(httpx, _request(url=url, method=method), state,
                       "gpt-6-sol", "low", True, False)

    assert state["send_attempts"] == 0


def test_effort_probe_rejects_nonpositive_output_cap():
    state = _state()

    with pytest.raises(_ProbeGate, match="output_limit_invalid"):
        _update_request(httpx, _request(max_output_tokens=-1), state,
                       "gpt-6-sol", "low", True, False)

    assert state["send_attempts"] == 0


def test_unobserved_socket_connect_is_blocked_before_network():
    from types import SimpleNamespace

    from scripts.hermes_codex_probe import _guard_socket_call

    calls = []
    permission = SimpleNamespace(active=False)
    guarded_connect = _guard_socket_call(
        lambda _sock, address: calls.append(address) or "connected", permission)

    with pytest.raises(OSError, match="probe_blocked_unobserved_socket_connect"):
        guarded_connect(object(), ("127.0.0.1", 9))
    assert calls == []

    permission.active = True
    assert guarded_connect(object(), ("chatgpt.com", 443)) == "connected"
    assert calls == [("chatgpt.com", 443)]


def test_async_httpx_transport_is_blocked_before_network():
    from scripts.hermes_codex_probe import _install_transport_guard

    state = {"catalog_requests": 0, "transport_attempts": 0}
    guarded = _install_transport_guard("gpt-6-sol", "low", False, state, False)
    try:
        blocked = httpx.AsyncClient.send(None, None)
        with pytest.raises(_ProbeGate, match="unobserved_async_transport_blocked"):
            blocked.send(None)
    finally:
        (httpx_module, client_send, async_send, iter_bytes,
         socket_module, connect, connect_ex) = guarded
        httpx_module.Client.send = client_send
        httpx_module.AsyncClient.send = async_send
        httpx_module.Response.iter_bytes = iter_bytes
        if connect is not None:
            socket_module.socket.connect = connect
        if connect_ex is not None:
            socket_module.socket.connect_ex = connect_ex


def test_availability_mode_uses_a_counted_fail_closed_classifier(monkeypatch):
    from types import SimpleNamespace

    import scripts.hermes_codex_probe as probe

    middleware = SimpleNamespace(_MODE_OVERRIDE="always", _classifier_factory=None)
    monkeypatch.setattr(probe.importlib, "import_module", lambda _name: middleware)
    loaded = SimpleNamespace(
        manifest=SimpleNamespace(path="C:/plugins/hermes-adaptive-effort"),
        enabled=True,
        module=SimpleNamespace(__package__="fake_plugin"),
    )
    state = {"classifier_calls": 0}

    probe._load_plugin_middleware(SimpleNamespace(_plugins={"plugin": loaded}),
                                  state, 0, False)

    assert middleware._MODE_OVERRIDE == "off"
    with pytest.raises(RuntimeError, match="classifier_disabled_for_availability"):
        middleware._classifier_factory().classify("synthetic text")
    assert state["classifier_calls"] == 1
