"""Bounded OpenAI Decisions API effort-scoring adapter."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from . import rubric
from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.openai.com/v1/decisions"
DEFAULT_MODEL = "gpt-6-luna"
SENTINEL_ENV = "OPENAI_API_KEY"


def _default_key_reader() -> str:
    """Resolve the OpenAI credential from Hermes secret scope, then environment."""
    try:
        from agent.secret_scope import get_secret
        value = get_secret(SENTINEL_ENV, "") or ""
        if str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return (os.environ.get(SENTINEL_ENV) or "").strip()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(request: urllib.request.Request, timeout: float):
    """POST once to the fixed API endpoint without following redirects."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class OpenAIDecisionClient:
    """Request one bounded rubric score; failures are returned without raising."""

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str = "",
                 timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 classification_instructions: str = "",
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.model = str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.api_key = (api_key or "").strip()
        self.endpoint = DEFAULT_ENDPOINT
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self.max_prompt_chars = int(max_prompt_chars or DEFAULT_MAX_PROMPT_CHARS)
        self.classification_instructions = rubric.normalize_classification_instructions(
            classification_instructions)
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
        key = self._key()
        if not key:
            logger.debug("hermes-adaptive-effort: no %s credential; skipping classification",
                         SENTINEL_ENV)
            return None, "credential_missing"

        body = {
            "model": self.model,
            "input": truncate_prompt(prompt, self.max_prompt_chars),
            "questions": [rubric.decisions_question(self.classification_instructions)],
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
        try:
            payload = (self._transport or _default_transport)(request, self.timeout)
        except urllib.error.HTTPError as exc:
            _close_response(exc)
            return None, "http_error"
        except urllib.error.URLError as exc:
            reason = "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) \
                else "transport_error"
            return None, reason
        except TimeoutError:
            return None, "timeout"
        except OSError:
            return None, "transport_error"
        except Exception:
            return None, "unexpected_error"

        status = getattr(payload, "status", None)
        if isinstance(status, int) and not 200 <= status < 300:
            _close_response(payload)
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

        score, failure = rubric.decisions_score(data)
        logger.debug("hermes-adaptive-effort: OpenAI Decisions completed in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, score is not None)
        return score, failure


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """Check credential readiness without returning the key or making a request."""
    try:
        return bool(str((key_reader or _default_key_reader)() or "").strip())
    except Exception:
        return False
