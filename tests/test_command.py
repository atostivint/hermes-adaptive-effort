"""``/hae`` and ``/hermes-adaptive-effort`` commands use Hermes' verified plugin API.

The command is the operator-facing surface for the plugin: ``status`` renders the
documented status payload, ``probe`` runs exactly one bounded classification.
Everything the command returns must stay free of prompt text (privacy contract).
"""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

init_module = import_plugin("__init__")
command = import_plugin("command")
middleware = import_plugin("middleware")


class RecordingCtx:
    """Fake ``PluginContext`` recording every registration the plugin asks for."""

    def __init__(self):
        self.middleware = []
        self.hooks = []
        self.commands = []

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))
        return object()

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))
        return object()

    def register_command(self, name, handler, description="", args_hint="", argument_mode=None):
        self.commands.append({
            "name": name,
            "handler": handler,
            "description": description,
            "args_hint": args_hint,
        })
        return object()

    def get_config(self, key, default=None):
        return default


def registered():
    ctx = RecordingCtx()
    init_module.register(ctx)
    return ctx


def use_settings(monkeypatch, settings):
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: settings.get(key, default))


def test_register_registers_short_and_compatibility_commands():
    ctx = registered()
    assert [c["name"] for c in ctx.commands] == ["hae", "hermes-adaptive-effort"]
    assert callable(ctx.commands[0]["handler"])
    assert ctx.commands[0]["description"].strip()


def test_register_registers_nothing_beyond_the_declared_surface():
    """Opt-in plugin: register() attaches exactly the documented registrations.

    Session lifecycle hooks retain turn status between turns and clear it at
    conversation boundaries. The subagent hooks only record which session is a
    child and the goal its parent wrote, and are inert until subagent_mode is enabled.
    """
    ctx = registered()
    assert [k for k, _ in ctx.middleware] == ["llm_request"]
    assert [k for k, _ in ctx.hooks] == [
        "on_session_end", "on_session_finalize", "on_session_reset",
        "subagent_start", "subagent_stop"]
    assert [c["name"] for c in ctx.commands] == ["hae", "hermes-adaptive-effort"]


def test_registered_handler_is_the_command_module_entry_point():
    ctx = registered()
    assert ctx.commands[0]["handler"] is command.handle


def test_usage_is_returned_for_empty_and_unknown_arguments():
    assert "usage" in command.handle("").lower()
    assert "usage" in command.handle("   ").lower()
    assert "usage" in command.handle("banana").lower()


def test_status_renders_the_configured_mode(monkeypatch):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"mode": "auto"}.get(key, default))
    out = command.handle("status")
    assert "mode" in out.lower()
    assert "auto" in out.split("mode", 1)[1].lower()
    assert out != command.handle("")  # not just the usage banner


def test_status_json_returns_the_documented_payload():
    payload = json.loads(command.handle("status json"))
    assert payload["schema"] == "hermes-adaptive-effort.status.v1"
    assert payload["plugin"] == "hermes-adaptive-effort"
    assert payload["mode"] in ("off", "recommend", "auto")
    for key in ("settings", "credential", "counts", "sessions", "last"):
        assert key in payload
    assert payload["settings"]["force_injection_models"] == []


def test_status_reports_selected_scorer_and_active_endpoint(monkeypatch):
    configured = {
        "scorer_provider": "openrouter",
        "scorer_model": "openai/gpt-4o-mini",
    }
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: configured.get(key, default))
    payload = json.loads(command.handle("status json"))
    assert payload["settings"]["scorer_provider"] == "openrouter"
    assert payload["settings"]["scorer_model_effective"] == "openai/gpt-4o-mini"
    assert payload["settings"]["endpoint_effective"] == \
        "https://openrouter.ai/api/v1/chat/completions"


def test_custom_scorer_status_redacts_endpoint_query_values(monkeypatch):
    configured = {
        "scorer_provider": "custom",
        "scorer_model": "local-rubric-4b",
        "custom_endpoint": "http://127.0.0.1:8080/v1/systemone?token=secret-custom-value&route=chosen-route",
        "custom_api_format": "systemone",
        "custom_auth": "none",
    }
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: configured.get(key, default))

    payload = json.loads(command.handle("status json"))
    rendered = json.dumps(payload)

    assert payload["settings"]["scorer_provider"] == "custom"
    assert payload["settings"]["scorer_model_effective"] == "local-rubric-4b"
    assert payload["settings"]["custom_endpoint"] == (
        "http://127.0.0.1:8080/v1/systemone?token=%5Bredacted%5D&route=%5Bredacted%5D")
    assert payload["credential_required"] is False
    assert "secret-custom-value" not in rendered and "chosen-route" not in rendered


def test_cloudflare_status_and_probe_show_only_safe_provider_details(monkeypatch):
    account_id = "0123456789abcdef0123456789abcdef"
    configured = {
        "scorer_provider": "cloudflare", "cloudflare_account_id": account_id}
    use_settings(monkeypatch, configured)
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **_kwargs: type("Scorer", (), {
                            "classify_detail": lambda _self, text: (1.0, None)})())
    payload = json.loads(command.handle("status json"))
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/clef"
    assert payload["settings"]["scorer_model_effective"] == "@cf/cloudflare/clef"
    assert payload["settings"]["endpoint_effective"] == endpoint
    assert payload["cloudflare_account_ready"] is True
    result = json.loads(command.handle("probe operator typed text"))
    assert result["score"] == 1.0
    assert "operator typed text" not in json.dumps(result)
    assert middleware.session_state() == {}


@pytest.mark.parametrize("api_mode, expected", [
    ("chat_completions", "cache-safe on chat_completions"),
    ("codex_responses", "cache-safe on codex_responses"),
    ("anthropic_messages", "cache-hostile on anthropic_messages"),
    (None, "unknown route (no api_mode)"),
])
def test_status_cache_safety_uses_last_observed_route(monkeypatch, api_mode, expected):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"mode": "auto"}.get(key, default))
    # No effort field: even unsupported requests must report the actual route.
    middleware.on_llm_request(
        request={"messages": [{"role": "user", "content": "hey"}]},
        session_id="MAIN", turn_id="turn", provider="example", model="model", api_mode=api_mode)
    payload = json.loads(command.handle("status json"))
    assert payload["last"]["api_mode"] == api_mode
    assert payload["cache_safety"].startswith(expected)
    assert f"cache safety: {expected}" in command.handle("status")


def test_status_json_reports_last_score_target_timing_and_failure(monkeypatch):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"mode": "auto"}.get(key, default))
    middleware.reset_state()
    middleware._remember("s1", {
        "state": "decided", "label": "high", "target": "high", "score": 1.9,
        "mode": "auto", "requests": 3, "probes": 1, "elapsed_ms": 412.5,
        "failure": None, "updated_at": 1000.0,
    }, 64)
    payload = json.loads(command.handle("status json"))
    [entry] = payload["sessions"]
    assert entry["session_id"] == "s1"
    assert entry["state"] == "decided"
    assert entry["score"] == 1.9
    assert entry["target"] == "high"
    assert entry["elapsed_ms"] == 412.5
    assert entry["requests"] == 3
    assert entry["probes"] == 1
    assert payload["last"]["session_id"] == "s1"
    assert payload["last"]["score"] == 1.9
    middleware.reset_state()


def test_status_json_exposes_exact_conversation_identity_for_turn_entries(monkeypatch):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"mode": "recommend"}.get(key, default))
    middleware.reset_state()
    middleware.on_llm_request(
        request={"messages": [{"role": "user", "content": "hello"}]},
        session_id="room/a", turn_id="turn-1", provider="example", model="model",
    )
    middleware.on_llm_request(
        request={"messages": [{"role": "user", "content": "hello"}]},
        session_id="room/another", turn_id="turn-1", provider="example", model="model",
    )

    payload = json.loads(command.handle("status json"))
    by_decision = {entry["session_id"]: entry for entry in payload["sessions"]}
    assert by_decision["room/a/turn-1"]["conversation_id"] == "room/a"
    assert by_decision["room/another/turn-1"]["conversation_id"] == "room/another"
    middleware.reset_state()


def test_status_json_reports_failure_reason_for_a_failed_session():
    middleware.reset_state()
    middleware._remember("s2", {
        "state": "failed", "label": None, "target": None, "score": None,
        "mode": "auto", "requests": 1, "probes": 1, "elapsed_ms": 12.0,
        "failure": "classifier_timeout", "updated_at": 1001.0,
    }, 64)
    payload = json.loads(command.handle("status json"))
    [entry] = payload["sessions"]
    assert entry["state"] == "failed"
    assert entry["failure"] == "classifier_timeout"
    middleware.reset_state()


def test_status_output_never_contains_prompt_text(monkeypatch):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"mode": "auto"}.get(key, default))
    marker = "UNIQUE_STATUS_PROMPT_MARKER"
    middleware.reset_state()
    middleware._remember("s1", {"state": "decided", "label": "high", "target": "high",
                                "prompt": marker}, 64)
    rendered = command.handle("status") + command.handle("status json")
    assert marker not in rendered
    middleware.reset_state()


def test_probe_without_text_is_usage_and_runs_no_classification(monkeypatch, no_network):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"mode": "auto"}.get(key, default))
    calls = []
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **kw: type("C", (), {"classify": lambda self, p: calls.append(p) or 1.0})())
    assert "usage" in command.handle("probe").lower()
    assert calls == []


def test_probe_reports_score_and_label(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **kw: type("C", (), {"classify": lambda self, p: 1.9})())
    payload = json.loads(command.handle("probe how do I rotate a k3s token"))
    assert payload["score"] == 1.9
    assert payload["label"] == "high"
    assert payload["failure"] is None
    assert payload["elapsed_ms"] >= 0


def test_probe_is_bounded_and_writes_no_session_state(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **kw: type("C", (), {"classify": lambda self, p: 1.0})())
    command.handle("probe anything at all")
    assert middleware.session_state() == {}


def test_probe_failure_is_reported_as_a_reason(monkeypatch, no_network):
    use_settings(monkeypatch, {"mode": "auto"})
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **kw: type("C", (), {"classify": lambda self, p: 1 / 0})())
    payload = json.loads(command.handle("probe something"))
    assert payload["failure"] == "classifier_error"
    assert payload["score"] is None


def test_settings_expose_the_effective_endpoint(monkeypatch):
    """A base URL in the config must be visible as such, not silently posted to."""
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"endpoint": "https://api.typesafe.ai/v1"}.get(key, default))
    settings = middleware._settings()
    assert settings["endpoint"] == "https://api.typesafe.ai/v1"
    assert settings["endpoint_effective"] == "https://api.typesafe.ai/v1/systemone"


def test_status_points_at_the_effective_endpoint_and_shows_the_raw_setting(monkeypatch):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {"endpoint": "https://api.typesafe.ai/v1"}.get(key, default))
    out = command.handle("status")
    assert "https://api.typesafe.ai/v1/systemone" in out
    assert "configured: https://api.typesafe.ai/v1" in out


def test_status_on_the_canonical_endpoint_does_not_add_a_configured_note(monkeypatch):
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: {
                            "endpoint": "https://api.typesafe.ai/v1/systemone"}.get(key, default))
    out = command.handle("status")
    assert "https://api.typesafe.ai/v1/systemone" in out
    assert "configured" not in out
