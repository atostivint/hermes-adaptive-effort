"""Provider selection is explicit and never silently crosses providers."""

from __future__ import annotations

from conftest import import_plugin

jev_client = import_plugin("jev_client")
openrouter_client = import_plugin("openrouter_client")
cloudflare_client = import_plugin("cloudflare_client")
scorers = import_plugin("scorers")
middleware = import_plugin("middleware")


def settings(**extra):
    values = middleware._settings()
    values.update(extra)
    return values


def test_jev_remains_the_default_scorer():
    client, failure = scorers.build_client(middleware._settings())
    assert failure is None
    assert isinstance(client, jev_client.JevClient)


def test_openrouter_requires_and_uses_the_configured_model():
    client, failure = scorers.build_client(settings(
        scorer_provider="openrouter", scorer_model="openai/gpt-4o-mini"))
    assert failure is None
    assert isinstance(client, openrouter_client.OpenRouterClient)
    assert client.model == "openai/gpt-4o-mini"


def test_openrouter_missing_model_fails_open_without_jev_fallback():
    client, failure = scorers.build_client(settings(scorer_provider="openrouter", scorer_model=""))
    assert client is None
    assert failure == "model_missing"


def test_cloudflare_uses_fixed_model_and_requires_a_valid_account():
    configured = settings(scorer_provider="cloudflare",
                          cloudflare_account_id="0123456789abcdef0123456789abcdef")
    client, failure = scorers.build_client(configured)
    assert failure is None
    assert isinstance(client, cloudflare_client.CloudflareClient)
    assert scorers.model_for("cloudflare", "ignored") == cloudflare_client.MODEL
    assert client.endpoint.endswith("/0123456789abcdef0123456789abcdef/ai/run/@cf/cloudflare/clef")
    assert scorers.build_client(settings(scorer_provider="cloudflare")) == (None, "account_missing")
    assert scorers.build_client(settings(scorer_provider="cloudflare",
        cloudflare_account_id="bad/path")) == (None, "account_invalid")


def test_unknown_provider_fails_open_without_jev_fallback():
    client, failure = scorers.build_client(settings(scorer_provider="another-provider"))
    assert client is None
    assert failure == "unsupported_provider"


def test_status_endpoint_and_model_follow_selected_scorer():
    jev_raw, jev_effective = scorers.endpoint_for("jev", "https://api.typesafe.ai/v1")
    assert jev_raw == "https://api.typesafe.ai/v1"
    assert jev_effective == "https://api.typesafe.ai/v1/systemone"
    endpoint, effective = scorers.endpoint_for("openrouter", "ignored")
    assert endpoint == effective == openrouter_client.DEFAULT_ENDPOINT
    assert scorers.model_for("jev", "ignored") == jev_client.JEV_MODEL
    assert scorers.model_for("openrouter", "openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    account_id = "0123456789abcdef0123456789abcdef"
    endpoint, effective = scorers.endpoint_for("cloudflare", "jev-endpoint", account_id)
    assert endpoint == effective == cloudflare_client.endpoint_for(account_id)
