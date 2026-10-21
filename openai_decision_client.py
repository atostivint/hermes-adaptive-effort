"""Bounded OpenAI Decisions API effort-scoring adapter."""

from __future__ import annotations

import logging
import time
import urllib.request
from typing import Any, Callable, Optional, Sequence

from . import rubric
from . import scorer_http
from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.openai.com/v1/decisions"
DEFAULT_MODEL = "gpt-6-luna"
SENTINEL_ENV = "OPENAI_API_KEY"


def _default_key_reader() -> str:
    """Resolve the OpenAI credential from Hermes secret scope, then environment."""
    return scorer_http.read_secret(SENTINEL_ENV)


_NoRedirectHandler = scorer_http.NoRedirectHandler
_close_response = scorer_http.close_response


def _default_transport(request: urllib.request.Request, timeout: float):
    """POST once to the fixed API endpoint without following redirects."""
    return scorer_http.default_transport(request, timeout)


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
        if self._key_reader is not None:
            try:
                return str(self._key_reader() or "").strip()
            except Exception:
                return ""
        return scorer_http.resolve_key("openai_decision", _default_key_reader)

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None, *,
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
            "input": truncate_prompt(prompt, self.max_prompt_chars),
            "questions": [rubric.continuation_decisions_question() if continuation else
                          rubric.decisions_question(self.classification_instructions, choices)],
        }
        request = scorer_http.build_request(
            self.endpoint, body, key=key, user_agent="hermes-adaptive-effort/0.1")

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
            score, failure = rubric.decisions_predicate(data)
        elif levels:
            score, failure = rubric.decisions_choice(data, levels)
        else:
            score, failure = rubric.decisions_score(data)
        logger.debug("hermes-adaptive-effort: OpenAI Decisions completed in %.0fms (valid=%s)",
                     (time.monotonic() - started) * 1000, score is not None)
        return score, failure

    def classify_continuation(self, prompt: Optional[str]):
        """``(probability, failure)`` of one separate yes/no continuation question."""
        return self.classify_detail(prompt, continuation=True)

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named choice from OpenAI Decisions."""
        return self.classify_detail(prompt, choices=choices)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """Check credential readiness without returning the key or making a request."""
    try:
        return bool(str((key_reader or _default_key_reader)() or "").strip())
    except Exception:
        return False
