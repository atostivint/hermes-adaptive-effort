"""Bounded OpenRouter chat-completions classifier.

This adapter is selected explicitly with ``scorer_provider=openrouter`` and a
configured ``scorer_model``. It sends only the bounded text being classified and
requests ZDR-only routing with data-collecting endpoints denied. The request still
contains the prompt and OpenRouter processes it. It fails open on missing
credentials, transport errors, or an invalid score.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt
from . import rubric

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
SENTINEL_ENV = "OPENROUTER_API_KEY"
MAX_COMPLETION_TOKENS = rubric.MAX_COMPLETION_TOKENS
_SYSTEM_PROMPT = rubric.CHAT_SYSTEM_PROMPT


def _default_key_reader() -> str:
    """Resolve the provider credential through Hermes' secret scope, then the env."""
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


class OpenRouterClient:
    """Request one strict 0..2 effort score from a configured OpenRouter model."""

    def __init__(self, *, model: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 endpoint: str = DEFAULT_ENDPOINT,
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.api_key = (api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self.max_prompt_chars = int(max_prompt_chars or DEFAULT_MAX_PROMPT_CHARS)
        self.endpoint = str(endpoint or DEFAULT_ENDPOINT).strip()
        self._transport = transport
        self._key_reader = key_reader

    def _key(self) -> str:
        if self.api_key:
            return self.api_key
        reader = self._key_reader or _default_key_reader
        try:
            return str(reader() or "").strip()
        except Exception:
            return ""

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str]) -> "tuple[Optional[float], Optional[str]]":
        if not isinstance(prompt, str) or not prompt.strip():
            return None, "invalid_prompt"
        if not self.model:
            return None, "model_missing"
        key = self._key()
        if not key:
            logger.debug("hermes-adaptive-effort: no %s credential; skipping classification",
                         SENTINEL_ENV)
            return None, "credential_missing"

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": truncate_prompt(prompt, self.max_prompt_chars)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": MAX_COMPLETION_TOKENS,
            "temperature": 0,
            "stream": False,
            "provider": {"zdr": True, "data_collection": "deny"},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "hermes-adaptive-effort/0.1",
            },
            method="POST",
        )
        started = time.monotonic()
        transport = self._transport or _default_transport
        try:
            payload = transport(request, self.timeout)
        except urllib.error.HTTPError:
            logger.debug("hermes-adaptive-effort: OpenRouter request failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "http_error"
        except urllib.error.URLError as exc:
            reason = "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) \
                else "transport_error"
            logger.debug("hermes-adaptive-effort: OpenRouter request failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, reason
        except TimeoutError:
            logger.debug("hermes-adaptive-effort: OpenRouter request failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "timeout"
        except OSError:
            logger.debug("hermes-adaptive-effort: OpenRouter request failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "transport_error"
        except Exception:
            logger.debug("hermes-adaptive-effort: OpenRouter request failed after %.0fms",
                         (time.monotonic() - started) * 1000)
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
        failure = None if score is not None else "malformed_response"
        logger.debug("hermes-adaptive-effort: OpenRouter classified in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, score is not None)
        return score, failure


def _extract_score(payload: Any) -> Optional[float]:
    """Extract only a finite numeric ``score`` in the shared 0..2 rubric."""
    return rubric.chat_completion_score(payload)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """Report whether an OpenRouter key resolves without returning its value."""
    try:
        reader = key_reader or _default_key_reader
        return bool(str(reader() or "").strip())
    except Exception:
        return False
