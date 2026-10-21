"""Bounded OpenRouter Decisions API classifier.

This adapter is selected explicitly with ``scorer_provider=openrouter`` and a
configured ``scorer_model`` that names an OpenRouter *decision model*. It sends the
shared System One rubric (``state.prompt`` plus ``questions``) to the Decisions
endpoint, bounded to the text being classified, and requests ZDR-only routing with
data-collecting endpoints denied. The request still contains the prompt and
OpenRouter processes it. It fails open on missing credentials, transport errors, or
an invalid answer.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from typing import Any, Callable, Optional, Sequence

from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt
from . import rubric
from . import scorer_http

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
SENTINEL_ENV = "OPENROUTER_API_KEY"
QUESTIONS = rubric.QUESTIONS


def _default_key_reader() -> str:
    """Resolve the provider credential through Hermes' secret scope, then the env."""
    return scorer_http.read_secret(SENTINEL_ENV)


def _default_transport(request: urllib.request.Request, timeout: float):
    return scorer_http.default_transport(request, timeout)


class OpenRouterClient:
    """Request one strict effort answer from a configured OpenRouter decision model."""

    def __init__(self, *, model: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 classification_instructions: str = "",
                 endpoint: str = DEFAULT_ENDPOINT,
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.api_key = (api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self.max_prompt_chars = int(max_prompt_chars or DEFAULT_MAX_PROMPT_CHARS)
        self.classification_instructions = rubric.normalize_classification_instructions(
            classification_instructions)
        self.endpoint = str(endpoint or DEFAULT_ENDPOINT).strip()
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
        return scorer_http.resolve_key("openrouter", _default_key_reader)

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None, *,
                        continuation: bool = False) -> "tuple[Any, Optional[str]]":
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
            "state": {"prompt": truncate_prompt(prompt, self.max_prompt_chars)},
            "questions": (rubric.continuation_questions() if continuation else
                          rubric.questions_for(self.classification_instructions, choices)),
            "provider": {"zdr": True, "data_collection": "deny"},
        }
        request = scorer_http.build_request(
            self.endpoint, body, key=key, user_agent="hermes-adaptive-effort/0.1")
        started = time.monotonic()
        payload, failure = scorer_http.send(request, self._transport or _default_transport,
                                            self.timeout)
        if failure:
            logger.debug("hermes-adaptive-effort: OpenRouter request failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, failure
        data, failure = scorer_http.read_json(payload)
        if failure:
            return None, failure
        levels = rubric.normalize_effort_choices(choices)
        if continuation:
            score, failure = rubric.systemone_noul(data)
        elif levels:
            score, failure = rubric.systemone_choice(data, levels)
        else:
            score, failure = rubric.systemone_score(data)
        logger.debug("hermes-adaptive-effort: OpenRouter classified in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, score is not None)
        return score, failure

    def classify_continuation(self, prompt: Optional[str]):
        """``(probability, failure)`` of one separate yes/no continuation question."""
        return self.classify_detail(prompt, continuation=True)

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named choice from the configured model."""
        return self.classify_detail(prompt, choices=choices)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """Report whether an OpenRouter key resolves without returning its value."""
    try:
        reader = key_reader or _default_key_reader
        return bool(str(reader() or "").strip())
    except Exception:
        return False
