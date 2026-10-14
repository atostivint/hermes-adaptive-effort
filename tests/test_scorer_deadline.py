"""``timeout_s`` 0 means the selected provider's default scoring deadline."""

from __future__ import annotations

import json

import pytest

from conftest import import_plugin

command = import_plugin("command")
middleware = import_plugin("middleware")
scorers = import_plugin("scorers")
jev_client = import_plugin("jev_client")

PROVIDER_DEFAULTS = {
    "jev": 2.0,
    "openrouter": 3.0,
    "cloudflare": 3.0,
    "openai_decision": 3.0,
    "custom": 10.0,
}


def use_settings(monkeypatch, values):
    monkeypatch.setattr(
        middleware, "_settings_provider", lambda key, default=None: values.get(key, default))


def test_default_setting_is_zero_and_table_covers_every_provider():
    assert middleware.DEFAULTS["timeout_s"] == 0
    assert set(scorers.PROVIDER_TIMEOUT_S) == set(scorers.PROVIDERS)
    assert scorers.PROVIDER_TIMEOUT_S == PROVIDER_DEFAULTS


@pytest.mark.parametrize("provider,expected", sorted(PROVIDER_DEFAULTS.items()))
def test_unset_timeout_uses_the_provider_default(monkeypatch, provider, expected):
    use_settings(monkeypatch, {"scorer_provider": provider})
    settings = middleware._settings()
    assert settings["timeout_s"] == expected
    assert settings["timeout_s_configured"] == 0
    assert settings["timeout_source"] == "provider_default"


def test_unknown_provider_uses_the_shared_adapter_default(monkeypatch):
    use_settings(monkeypatch, {"scorer_provider": "nope"})
    assert middleware._settings()["timeout_s"] == jev_client.DEFAULT_TIMEOUT_S


@pytest.mark.parametrize("provider", sorted(PROVIDER_DEFAULTS))
@pytest.mark.parametrize("value", [5.0, 3.0, "7"])
def test_operator_value_wins_including_explicit_three(monkeypatch, provider, value):
    use_settings(monkeypatch, {"scorer_provider": provider, "timeout_s": value})
    settings = middleware._settings()
    assert settings["timeout_s"] == float(value)
    assert settings["timeout_s_configured"] == float(value)
    assert settings["timeout_source"] == "configured"


@pytest.mark.parametrize("value", [0, 0.0, -1, "-2.5", "abc", None, "", float("nan"), float("inf")])
def test_zero_negative_or_invalid_falls_back_to_the_provider_default(monkeypatch, value):
    use_settings(monkeypatch, {"scorer_provider": "custom", "timeout_s": value})
    settings = middleware._settings()
    assert settings["timeout_s"] == 10.0
    assert settings["timeout_s_configured"] == 0
    assert settings["timeout_source"] == "provider_default"


def test_switching_provider_changes_the_effective_deadline(monkeypatch):
    values = {"scorer_provider": "jev"}
    use_settings(monkeypatch, values)
    assert middleware._settings()["timeout_s"] == 2.0
    values["scorer_provider"] = "custom"
    assert middleware._settings()["timeout_s"] == 10.0


def test_effective_deadline_reaches_the_built_client(monkeypatch):
    use_settings(monkeypatch, {"scorer_provider": "jev"})
    client, failure = scorers.build_client(middleware._settings())
    assert failure is None and client.timeout == 2.0
    use_settings(monkeypatch, {"scorer_provider": "jev", "timeout_s": 4.5})
    client, _ = scorers.build_client(middleware._settings())
    assert client.timeout == 4.5


def test_effective_deadline_reaches_the_probe_factory(monkeypatch, no_network):
    seen = []

    def factory(timeout=None, **_kw):
        seen.append(timeout)
        return type("C", (), {"classify": lambda self, p: 1.0})()

    use_settings(monkeypatch, {"mode": "auto", "scorer_provider": "custom"})
    monkeypatch.setattr(middleware, "_classifier_factory", factory)
    json.loads(command.handle("probe hello"))
    assert seen == [10.0]


def test_status_text_and_json_report_the_source(monkeypatch):
    use_settings(monkeypatch, {"scorer_provider": "jev"})
    assert "Timeout: 2.0 s (provider default)" in command.handle("status full")
    payload = json.loads(command.handle("status json"))
    assert payload["settings"]["timeout_s"] == 2.0
    assert payload["settings"]["timeout_source"] == "provider_default"

    use_settings(monkeypatch, {"scorer_provider": "jev", "timeout_s": 5})
    assert "Timeout: 5.0 s (configured)" in command.handle("status full")
    payload = json.loads(command.handle("status json"))
    assert payload["settings"]["timeout_s"] == 5.0
    assert payload["settings"]["timeout_source"] == "configured"
