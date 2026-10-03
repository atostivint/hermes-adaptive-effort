"""Bounded Cloudflare Workers AI Clef scoring adapter."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, QUESTIONS, truncate_prompt

logger = logging.getLogger(__name__)

API_ROOT = "https://api.cloudflare.com/client/v4/accounts"
MODEL = "@cf/cloudflare/clef"
MODEL_SELECTOR = "clef"
SENTINEL_ENV = "CLOUDFLARE_AUTH_TOKEN"
_ACCOUNT_ID = re.compile(r"^[0-9a-fA-F]{32}$")


def valid_account_id(account_id: Any) -> bool:
    return isinstance(account_id, str) and bool(_ACCOUNT_ID.fullmatch(account_id.strip()))


def endpoint_for(account_id: Any) -> str:
    """Return the fixed account-scoped route, or a safe placeholder if invalid."""
    if not valid_account_id(account_id):
        return API_ROOT + "/{account_id}/ai/run/" + MODEL
    return f"{API_ROOT}/{account_id.strip()}/ai/run/{MODEL}"


def _default_key_reader() -> str:
    try:
        from agent.secret_scope import get_secret
        value = get_secret(SENTINEL_ENV, "") or ""
        if str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return (os.environ.get(SENTINEL_ENV) or "").strip()


def _default_transport(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


class CloudflareClient:
    """Ask Clef for one score using the shared effort rubric."""

    def __init__(self, *, account_id: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.account_id = str(account_id or "").strip()
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self.max_prompt_chars = int(max_prompt_chars or DEFAULT_MAX_PROMPT_CHARS)
        self.endpoint = endpoint_for(self.account_id)
        self._transport = transport
        self._key_reader = key_reader

    def _key(self) -> str:
        if self.api_key:
            return self.api_key
        try:
            return str((self._key_reader or _default_key_reader)() or "").strip()
        except Exception:
            return ""

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str]) -> "tuple[Optional[float], Optional[str]]":
        if not isinstance(prompt, str) or not prompt.strip():
            return None, "invalid_prompt"
        if not valid_account_id(self.account_id):
            return None, "account_missing" if not self.account_id else "account_invalid"
        key = self._key()
        if not key:
            logger.debug("hermes-adaptive-effort: no %s credential; skipping classification",
                         SENTINEL_ENV)
            return None, "credential_missing"

        body = {
            "model": MODEL_SELECTOR,
            "state": {"prompt": truncate_prompt(prompt, self.max_prompt_chars)},
            "questions": QUESTIONS,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Authorization": f"Bearer {key}",
                     "User-Agent": "hermes-adaptive-effort/0.1"},
            method="POST",
        )
        started = time.monotonic()
        try:
            payload = (self._transport or _default_transport)(request, self.timeout)
        except urllib.error.HTTPError:
            return None, "http_error"
        except urllib.error.URLError as exc:
            return None, "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) else "transport_error"
        except TimeoutError:
            return None, "timeout"
        except OSError:
            return None, "transport_error"
        except Exception:
            return None, "unexpected_error"

        status = getattr(payload, "status", None)
        if isinstance(status, int) and not 200 <= status < 300:
            return None, "http_error"
        try:
            if hasattr(payload, "__enter__"):
                with payload as response:
                    data = json.load(response) if hasattr(response, "read") else response
            elif hasattr(payload, "read"):
                data = json.load(payload)
            else:
                data = payload
        except Exception:
            return None, "malformed_response"
        score = _extract_score(data)
        logger.debug("hermes-adaptive-effort: Cloudflare classified in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, score is not None)
        return (score, None) if score is not None else (None, "malformed_response")


def _extract_score(payload: Any) -> Optional[float]:
    """Parse only a successful REST wrapper and a finite score in the shared 0..2 range."""
    try:
        if (not isinstance(payload, dict) or payload.get("success") is not True
                or payload.get("errors")):
            return None
        raw = payload["result"]["answers"]["effort"]["score"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        score = float(raw)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0.0 <= score <= 2.0 else None


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    try:
        return bool(str((key_reader or _default_key_reader)() or "").strip())
    except Exception:
        return False
