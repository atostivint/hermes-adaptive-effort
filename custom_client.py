"""Configurable rubric scorer for systemone and chat-completions endpoints."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional, Tuple

from . import rubric
from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt

logger = logging.getLogger(__name__)
QUESTIONS = rubric.QUESTIONS

SENTINEL_ENV = "CUSTOM_SCORER_API_KEY"
API_FORMATS = ("systemone", "chat_completions")
AUTH_MODES = ("none", "bearer")


def validate_endpoint(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(normalized URL, failure)`` for a complete safe HTTP(S) endpoint."""
    endpoint = str(value or "").strip()
    if not endpoint:
        return None, "endpoint_missing"
    try:
        parts = urllib.parse.urlsplit(endpoint)
        # Accessing .port detects malformed/non-numeric/out-of-range port values.
        _ = parts.port
        valid = (parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
                 and "@" not in parts.netloc and "#" not in endpoint
                 and not any(char.isspace() for char in endpoint))
    except (ValueError, UnicodeError):
        valid = False
        parts = None
    if not valid or parts is None:
        return None, "endpoint_invalid"
    return endpoint, None


def safe_endpoint_display(value: Any) -> str:
    """Keep endpoint structure visible while replacing every query value."""
    endpoint, failure = validate_endpoint(value)
    if failure or endpoint is None:
        return "[invalid endpoint]"
    parts = urllib.parse.urlsplit(endpoint)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    safe_query = urllib.parse.urlencode([(name, "[redacted]") for name, _ in query])
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))


def _default_key_reader() -> str:
    """Resolve the optional custom bearer key through secret scope, then env."""
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
    # The standard urlopen opener follows redirects. A provider endpoint must be
    # called exactly as configured, especially when its URL contains a query key.
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


class CustomClient:
    """Make one bounded rubric request to an explicitly configured endpoint."""

    def __init__(self, *, endpoint: str, model: str, api_format: str = "systemone",
                 auth: str = "none", api_key: str = "", timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 classification_instructions: str = "",
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.endpoint = str(endpoint or "").strip()
        self.model = str(model or "").strip()
        self.api_format = str(api_format or "").strip().lower()
        self.auth = str(auth or "").strip().lower()
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self.max_prompt_chars = int(max_prompt_chars or DEFAULT_MAX_PROMPT_CHARS)
        self.classification_instructions = rubric.normalize_classification_instructions(
            classification_instructions)
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
        endpoint, failure = validate_endpoint(self.endpoint)
        if failure:
            return None, failure
        if self.api_format not in API_FORMATS:
            return None, "unsupported_api_format"
        if self.auth not in AUTH_MODES:
            return None, "unsupported_auth"
        if not self.model:
            return None, "model_missing"

        key = ""
        if self.auth == "bearer":
            key = self._key()
            if not key:
                return None, "credential_missing"

        prompt_text = truncate_prompt(prompt, self.max_prompt_chars)
        if self.api_format == "systemone":
            body = {"state": {"prompt": prompt_text}, "model": self.model,
                    "questions": rubric.questions_for(self.classification_instructions)}
        else:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": rubric.chat_system_prompt(
                        self.classification_instructions)},
                    {"role": "user", "content": prompt_text},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": rubric.MAX_COMPLETION_TOKENS,
                "temperature": 0,
                "stream": False,
            }
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "User-Agent": "hermes-adaptive-effort/0.1"}
        if self.auth == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(
            endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")

        started = time.monotonic()
        try:
            payload = (self._transport or _default_transport)(request, self.timeout)
        except urllib.error.HTTPError:
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

        if self.api_format == "systemone":
            score, failure = rubric.systemone_score(data)
        else:
            score = rubric.chat_completion_score(data)
            failure = None if score is not None else "malformed_response"
        logger.debug("hermes-adaptive-effort: custom scorer completed in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, score is not None)
        return score, failure


def credential_present(auth: Any = "none",
                       key_reader: Optional[Callable[[], str]] = None) -> bool:
    """Report readiness without returning a key; auth=none never reads one."""
    selected = str(auth or "none").strip().lower()
    if selected == "none":
        return True
    if selected != "bearer":
        return False
    try:
        return bool(str((key_reader or _default_key_reader)() or "").strip())
    except Exception:
        return False
