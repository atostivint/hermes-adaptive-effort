"""Explicit provider selection for effort-scoring adapters.

The request-routing middleware depends only on this small registry; it never
falls back to a different scorer when the selected provider fails.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from . import cloudflare_client, jev_client, openrouter_client

JEV = "jev"
OPENROUTER = "openrouter"
CLOUDFLARE = "cloudflare"
PROVIDERS = (JEV, OPENROUTER, CLOUDFLARE)


def build_client(settings: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
    """Build only the explicitly configured adapter; errors are fail-open codes."""
    provider = str(settings.get("scorer_provider") or JEV).strip().lower()
    if provider == JEV:
        try:
            return jev_client.JevClient(
                timeout=settings["timeout_s"],
                endpoint=settings["endpoint"],
                max_prompt_chars=settings["prompt_chars"],
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
            ), None
        except Exception:
            return None, "classifier_error"
    return None, "unsupported_provider"


def credential_present(provider: Any = JEV) -> bool:
    """Check the selected adapter's credential without exposing the secret."""
    selected = str(provider or JEV).strip().lower()
    if selected == JEV:
        return jev_client.credential_present()
    if selected == OPENROUTER:
        return openrouter_client.credential_present()
    if selected == CLOUDFLARE:
        return cloudflare_client.credential_present()
    return False


def endpoint_for(provider: Any, jev_endpoint: Any, cloudflare_account_id: Any = "",
                 cloudflare_model: Any = cloudflare_client.DEFAULT_MODEL_SELECTOR) -> Tuple[str, str]:
    """Return the raw and effective endpoint for the selected scorer."""
    selected = str(provider or JEV).strip().lower()
    if selected == OPENROUTER:
        endpoint = openrouter_client.DEFAULT_ENDPOINT
        return endpoint, endpoint
    if selected == CLOUDFLARE:
        endpoint = cloudflare_client.endpoint_for(cloudflare_account_id, cloudflare_model)
        return endpoint, endpoint
    raw = str(jev_endpoint or jev_client.DEFAULT_ENDPOINT)
    return raw, jev_client.normalize_endpoint(raw)


def model_for(provider: Any, configured_model: Any,
              cloudflare_model: Any = cloudflare_client.DEFAULT_MODEL_SELECTOR) -> str:
    """Return the model reported in status for the selected scorer."""
    selected = str(provider or JEV).strip().lower()
    if selected == JEV:
        return jev_client.JEV_MODEL
    if selected == CLOUDFLARE:
        return cloudflare_client.model_path_for(cloudflare_model)
    return str(configured_model or "").strip()
