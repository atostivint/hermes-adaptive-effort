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

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional, Sequence

from . import rubric
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


def _read_secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret
        value = get_secret(name, "") or ""
        if str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return (os.environ.get(name) or "").strip()


def _default_key_reader() -> str:
    """``NACE_API_KEY`` wins; the legacy ``DREX_API_KEY`` is the fallback."""
    return _read_secret(SENTINEL_ENV) or _read_secret(LEGACY_SENTINEL_ENV)


def _default_transport(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


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
        try:
            return str((self._key_reader or _default_key_reader)() or "").strip()
        except Exception:
            return ""

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None) -> "tuple[Any, Optional[str]]":
        """``(score, failure)``; ``failure`` is a reason code, never prompt text."""
        try:
            return self._classify_detail(prompt, choices)
        except Exception:
            logger.debug("hermes-adaptive-effort: Nace classification raised; failing open")
            return None, "unexpected_error"

    def _classify_detail(self, prompt: Optional[str],
                         choices: Optional[Sequence[str]]) -> "tuple[Any, Optional[str]]":
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
            "questions": rubric.questions_for(self.classification_instructions, choices),
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
        except urllib.error.HTTPError as exc:
            # 401 key, 402 credit, 422 request, 429 rate limit, 5xx server: all fail
            # open for this turn; the scorer is not retried (see invariant 4).
            logger.debug("hermes-adaptive-effort: Nace returned HTTP %s", exc.code)
            return None, "http_error"
        except urllib.error.URLError as exc:
            return None, "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) \
                else "transport_error"
        except TimeoutError:
            return None, "timeout"
        except OSError:
            return None, "transport_error"
        except Exception:
            return None, "unexpected_error"

        status = getattr(payload, "status", None)
        if isinstance(status, int) and not 200 <= status < 300:
            logger.debug("hermes-adaptive-effort: Nace returned HTTP %d", status)
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

        levels = rubric.normalize_effort_choices(choices)
        if levels:
            result, failure = rubric.systemone_choice(data, levels)
        else:
            result, failure = rubric.systemone_score(data)
        logger.debug("hermes-adaptive-effort: Nace classified in %.0fms (valid=%s, failure=%s)",
                     (time.monotonic() - started) * 1000, result is not None, failure)
        return result, failure

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named level using a System One choice question."""
        return self.classify_detail(prompt, choices=choices)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """True when a Nace credential resolves; never returns the secret."""
    try:
        return bool(str((key_reader or _default_key_reader)() or "").strip())
    except Exception:
        return False
