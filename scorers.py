"""Explicit provider selection for effort-scoring adapters.

The request-routing middleware depends only on this small registry; it never
falls back to a different scorer when the selected provider fails.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from . import cloudflare_client, custom_client, jev_client, openrouter_client

JEV = "jev"
OPENROUTER = "openrouter"
CLOUDFLARE = "cloudflare"
CUSTOM = "custom"
PROVIDERS = (JEV, OPENROUTER, CLOUDFLARE, CUSTOM)


def build_client(settings: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    """Build only the explicitly configured adapter; errors are fail-open codes."""
    provider = str(settings.get("scorer_provider") or JEV).strip().lower()
    if provider == JEV:
        try:
            return jev_client.JevClient(
                timeout=settings["timeout_s"],
                endpoint=settings["endpoint"],
                model=settings.get("jev_model", jev_client.JEV_MODEL),
                max_prompt_chars=settings["prompt_chars"],
                classification_instructions=settings.get("classification_instructions", ""),
            ), None
        except Exception:
            return None, "classifier_error"
    if provider == OPENROUTER:
        model = str(settings.get("scorer_model") or "").strip()
        if not model:
            return None, "model_missing"
        try:
            return openrouter_client.OpenRouterClient(
                model=model,
                timeout=settings["timeout_s"],
                max_prompt_chars=settings["prompt_chars"],
                classification_instructions=settings.get("classification_instructions", ""),
            ), None
        except Exception:
            return None, "classifier_error"
    if provider == CLOUDFLARE:
        account_id = str(settings.get("cloudflare_account_id") or "").strip()
        if not cloudflare_client.valid_account_id(account_id):
            return None, "account_missing" if not account_id else "account_invalid"
        try:
            return cloudflare_client.CloudflareClient(
                account_id=account_id,
                model_selector=settings.get(
                    "cloudflare_model", cloudflare_client.DEFAULT_MODEL_SELECTOR),
                timeout=settings["timeout_s"],
                max_prompt_chars=settings["prompt_chars"],
                classification_instructions=settings.get("classification_instructions", ""),
            ), None
        except Exception:
            return None, "classifier_error"
    if provider == CUSTOM:
        model = str(settings.get("scorer_model") or "").strip()
        if not model:
            return None, "model_missing"
        try:
            return custom_client.CustomClient(
                endpoint=settings.get("custom_endpoint", ""),
                model=model,
                api_format=settings.get("custom_api_format", "systemone"),
                auth=settings.get("custom_auth", "none"),
                timeout=settings["timeout_s"],
                max_prompt_chars=settings["prompt_chars"],
                classification_instructions=settings.get("classification_instructions", ""),
            ), None
        except Exception:
            return None, "classifier_error"
    return None, "unsupported_provider"


def credential_present(provider: Any = JEV, custom_auth: Any = "none") -> bool:
    """Check the selected adapter's credential without exposing the secret."""
    selected = str(provider or JEV).strip().lower()
    if selected == JEV:
        return jev_client.credential_present()
    if selected == OPENROUTER:
        return openrouter_client.credential_present()
    if selected == CLOUDFLARE:
        return cloudflare_client.credential_present()
    if selected == CUSTOM and str(custom_auth or "none").strip().lower() == "bearer":
        return custom_client.credential_present()
    return False


def credential_required(provider: Any = JEV, custom_auth: Any = "none") -> bool:
    """Whether this scorer's configured transport needs an API credential."""
    selected = str(provider or JEV).strip().lower()
    if selected == CUSTOM:
        return str(custom_auth or "none").strip().lower() == "bearer"
    return selected in (JEV, OPENROUTER, CLOUDFLARE)


def endpoint_for(provider: Any, jev_endpoint: Any, cloudflare_account_id: Any = "",
                 cloudflare_model: Any = cloudflare_client.DEFAULT_MODEL_SELECTOR,
                 custom_endpoint: Any = "") -> Tuple[str, str]:
    """Return the raw and effective endpoint for the selected scorer."""
    selected = str(provider or JEV).strip().lower()
    if selected == OPENROUTER:
        endpoint = openrouter_client.DEFAULT_ENDPOINT
        return endpoint, endpoint
    if selected == CLOUDFLARE:
        endpoint = cloudflare_client.endpoint_for(cloudflare_account_id, cloudflare_model)
        return endpoint, endpoint
    if selected == CUSTOM:
        endpoint = str(custom_endpoint or "").strip()
        return endpoint, endpoint
    raw = str(jev_endpoint or jev_client.DEFAULT_ENDPOINT)
    return raw, jev_client.normalize_endpoint(raw)


def model_for(provider: Any, configured_model: Any,
              cloudflare_model: Any = cloudflare_client.DEFAULT_MODEL_SELECTOR,
              jev_model: Any = jev_client.JEV_MODEL) -> str:
    """Return the model reported in status for the selected scorer."""
    selected = str(provider or JEV).strip().lower()
    if selected == JEV:
        return str(jev_model or jev_client.JEV_MODEL).strip() or jev_client.JEV_MODEL
    if selected == CLOUDFLARE:
        return cloudflare_client.model_path_for(cloudflare_model)
    return str(configured_model or "").strip()


def safe_endpoint_display(value: Any) -> str:
    """Return the URL suitable for status output without query parameter values."""
    return custom_client.safe_endpoint_display(value)
