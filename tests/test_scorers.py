"""Provider selection is explicit and never silently crosses providers."""

from __future__ import annotations

from conftest import import_plugin

jev_client = import_plugin("jev_client")
openrouter_client = import_plugin("openrouter_client")
cloudflare_client = import_plugin("cloudflare_client")
custom_client = import_plugin("custom_client")
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


def test_custom_provider_uses_configured_endpoint_model_format_and_auth():
    configured = settings(
        scorer_provider="custom",
        scorer_model="local-rubric-4b",
        custom_endpoint="http://127.0.0.1:8080/v1/chat/completions",
        custom_api_format="chat_completions",
        custom_auth="none",
    )

    client, failure = scorers.build_client(configured)

    assert failure is None
    assert isinstance(client, custom_client.CustomClient)
    assert client.endpoint == configured["custom_endpoint"]
    assert client.model == "local-rubric-4b"
    assert client.api_format == "chat_completions"
    assert client.auth == "none"
    assert scorers.model_for("custom", configured["scorer_model"]) == "local-rubric-4b"
    assert scorers.endpoint_for(
        "custom", "ignored", custom_endpoint=configured["custom_endpoint"]
    ) == (configured["custom_endpoint"], configured["custom_endpoint"])
    assert scorers.credential_required("custom", "none") is False


def test_unknown_provider_fails_open_without_jev_fallback():
    client, failure = scorers.build_client(settings(scorer_provider="another-provider"))
    assert client is None
    assert failure == "unsupported_provider"


def test_classifier_guidance_is_shared_by_every_provider():
    guidance = "Prefer high when several independent constraints interact."
    common = {"classification_instructions": guidance}
    configurations = (
        settings(**common),
        settings(**common, scorer_provider="openrouter", scorer_model="openai/gpt-4o-mini"),
        settings(**common, scorer_provider="cloudflare",
                 cloudflare_account_id="0123456789abcdef0123456789abcdef"),
        settings(**common, scorer_provider="custom", scorer_model="local-model",
                 custom_endpoint="http://127.0.0.1:8080/v1/systemone"),
    )

    for configured in configurations:
        client, failure = scorers.build_client(configured)
        assert failure is None
        assert client.classification_instructions == guidance


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
