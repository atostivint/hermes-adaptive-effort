"""Bounded Nace Drex scoring adapter.

Verified contract (source: https://console.nace.ai/llms-full.txt):

* Endpoint: ``https://console.nace.ai`` + ``/v1/systemone``; ``NACE_BASE_URL``
  overrides the base.
* Request body: ``{"model": "drex-v2.0", "state": {...}, "questions": {...}}`` —
  the same System One shape Jev uses, so the shared rubric is reused unchanged.
* A ``score`` answer is ``answers.effort.score`` (expected level, may fall
  between two levels); ``confidence`` is not the score. A ``choice`` answer is
  ``answers.effort.choice``.
* Over-long requests are answered by an older Drex model and the response
  ``model`` reports it; this adapter never compares it with the requested model.
* Credential: ``NACE_API_KEY`` (legacy ``DREX_API_KEY`` also accepted), resolved
  through Hermes' secret scope first, then the environment.

Everything here is fail-open: any error returns ``None`` plus a reason code from
the table in :mod:`jev_client`. The transport is injectable so unit tests never
open a socket.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from typing import Any, Callable, Optional, Sequence

from . import rubric
from . import scorer_http
from .jev_client import (DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, normalize_endpoint
                         as _normalize_systemone_endpoint, truncate_prompt)

logger = logging.getLogger(__name__)
QUESTIONS = rubric.QUESTIONS

DEFAULT_BASE_URL = "https://console.nace.ai"
DEFAULT_ENDPOINT = DEFAULT_BASE_URL + "/v1/systemone"
BASE_URL_ENV = "NACE_BASE_URL"
SENTINEL_ENV = "NACE_API_KEY"
LEGACY_SENTINEL_ENV = "DREX_API_KEY"
MODEL = "drex-v2.0"


def normalize_endpoint(value: Any) -> str:
    """Return the URL to POST to; a bare host or ``/v1`` base gets ``/v1/systemone``.

    A blank value is the documented default. A URL that already carries a
    non-version path is used verbatim (same tolerance as the Jev endpoint).
    """
    url = str(value or "").strip()
    if not url:
        return DEFAULT_ENDPOINT
    return _normalize_systemone_endpoint(url)


def configured_base_url() -> str:
    """The raw ``NACE_BASE_URL`` override, or the documented default base."""
    return (os.environ.get(BASE_URL_ENV) or "").strip() or DEFAULT_BASE_URL


def endpoint_for() -> str:
    """The effective scoring route for the current environment."""
    return normalize_endpoint(configured_base_url())


_read_secret = scorer_http.read_secret


def _default_key_reader() -> str:
    """``NACE_API_KEY`` wins; the legacy ``DREX_API_KEY`` is the fallback."""
    return _read_secret(SENTINEL_ENV) or _read_secret(LEGACY_SENTINEL_ENV)


def _default_transport(request: urllib.request.Request, timeout: float):
    return scorer_http.default_transport(request, timeout)


class NaceClient:
    """Ask Drex one score or named-choice question. Never raises out of ``classify``."""

    def __init__(self, *, api_key: str = "", endpoint: str = "", model: str = MODEL,
                 timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 classification_instructions: str = "",
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.api_key = (api_key or "").strip()
        self.endpoint = normalize_endpoint(endpoint)
        self.model = str(model or MODEL).strip() or MODEL
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
        return scorer_http.resolve_key("nace", _default_key_reader)

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None, *,
                        continuation: bool = False) -> "tuple[Any, Optional[str]]":
        """``(score, failure)``; ``failure`` is a reason code, never prompt text."""
        try:
            return self._classify_detail(prompt, choices, continuation)
        except Exception:
            logger.debug("hermes-adaptive-effort: Nace classification raised; failing open")
            return None, "unexpected_error"

    def _classify_detail(self, prompt: Optional[str], choices: Optional[Sequence[str]],
                         continuation: bool = False) -> "tuple[Any, Optional[str]]":
        if not isinstance(prompt, str) or not prompt.strip():
            return None, "invalid_prompt"
        key = self._key()
        if not key:
            logger.debug("hermes-adaptive-effort: no %s credential; skipping classification",
                         SENTINEL_ENV)
            return None, "credential_missing"

        body = {
            "model": self.model,
            "state": {"prompt": truncate_prompt(prompt, self.max_prompt_chars)},
            "questions": (rubric.continuation_questions() if continuation else
                          rubric.questions_for(self.classification_instructions, choices)),
        }
        request = scorer_http.build_request(
            self.endpoint, body, key=key, user_agent="hermes-adaptive-effort/0.1")
        started = time.monotonic()
        payload, failure = scorer_http.send(request, self._transport or _default_transport,
                                            self.timeout)
        if failure:
            # 401 key, 402 credit, 422 request, 429 rate limit, 5xx server: all fail
            # open for this turn; only a transient status is retried (scorer_retry).
            logger.debug("hermes-adaptive-effort: Nace request failed (%s)", failure)
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
        logger.debug("hermes-adaptive-effort: Nace classified in %.0fms (valid=%s, failure=%s)",
                     (time.monotonic() - started) * 1000, result is not None, failure)
        return result, failure

    def classify_continuation(self, prompt: Optional[str]):
        """``(probability, failure)`` of one separate yes/no continuation question."""
        return self.classify_detail(prompt, continuation=True)

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named level using a System One choice question."""
        return self.classify_detail(prompt, choices=choices)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """True when a Nace credential resolves; never returns the secret."""
    try:
        return bool(str((key_reader or _default_key_reader)() or "").strip())
    except Exception:
        return False
