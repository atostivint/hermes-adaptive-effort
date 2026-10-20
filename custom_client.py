"""Configurable rubric scorer for System One endpoints."""

from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional, Sequence, Tuple

from . import rubric
from . import scorer_http
from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt

logger = logging.getLogger(__name__)
QUESTIONS = rubric.QUESTIONS

SENTINEL_ENV = "CUSTOM_SCORER_API_KEY"
API_FORMATS = ("systemone",)
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
    return scorer_http.read_secret(SENTINEL_ENV)


_NoRedirectHandler = scorer_http.NoRedirectHandler


def _default_transport(request: urllib.request.Request, timeout: float):
    # A provider endpoint must be called exactly as configured, especially when its
    # URL contains a query key: no redirect is followed.
    return scorer_http.default_transport(request, timeout)


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
        if self._key_reader is not None:
            try:
                return str(self._key_reader() or "").strip()
            except Exception:
                return ""
        return scorer_http.resolve_key("custom", _default_key_reader)

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None, *,
                        continuation: bool = False) -> "tuple[Any, Optional[str]]":
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

        body = {"state": {"prompt": truncate_prompt(prompt, self.max_prompt_chars)},
                "model": self.model,
                "questions": (rubric.continuation_questions() if continuation else
                              rubric.questions_for(self.classification_instructions, choices))}
        request = scorer_http.build_request(
            endpoint, body, key=key, user_agent="hermes-adaptive-effort/0.1")

        started = time.monotonic()
        payload, failure = scorer_http.send(request, self._transport or _default_transport,
                                            self.timeout)
        if failure:
            return None, failure
        data, failure = scorer_http.read_json(payload)
        if failure:
            return None, failure

        levels = rubric.normalize_effort_choices(choices)
        if continuation:
            result, failure = rubric.systemone_noul(data)
        elif levels:
            result, failure = rubric.systemone_choice(data, levels)
        else:
            result, failure = rubric.systemone_score(data)
        logger.debug("hermes-adaptive-effort: custom scorer completed in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, result is not None)
        return result, failure

    def classify_continuation(self, prompt: Optional[str]):
        """``(probability, failure)`` of one separate yes/no continuation question."""
        return self.classify_detail(prompt, continuation=True)

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named effort through this custom protocol."""
        return self.classify_detail(prompt, choices=choices)


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
