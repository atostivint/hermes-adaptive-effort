"""Bounded local Ollama scoring adapter (no credential, no prompt leaves a local endpoint).

Documented contract (issue #33; Ollama's OpenAI-compatible surface, not exercised by a live
call in this repository's tests):

* Endpoint: the operator's Ollama base, default ``http://localhost:11434``; the adapter POSTs
  to ``<base>/v1/chat/completions``. A base that already ends with that path is used verbatim.
* Request body: ``model``, ``messages``, ``max_tokens: 1``, ``temperature: 0``,
  ``logprobs: true``, ``top_logprobs: 5`` and ``stream: false``. One generated token is asked
  for, so the answer is a distribution over the next token rather than prose.
* No ``Authorization``, cookie or API-key header at all: Ollama is unauthenticated by default.
  An operator reverse proxy that demands a key is out of this adapter's scope.
* No thinking toggle is sent. The OpenAI-compatible surface this adapter speaks documents no
  portable one (``think`` belongs to Ollama's native ``/api/chat``), so a thinking model can
  spend the single requested token on reasoning; that answer carries no allowed letter and
  fails open as ``malformed_response`` instead of being misread.
* The answer is read from ``choices[0].logprobs.content[0].top_logprobs``: only tokens that
  normalise to an allowed answer are kept and the highest logprob wins. A tie between two
  different answers, an empty list, a missing ``logprobs``, or a token outside the allowed set
  is a strict ``malformed_response``. A received answer is never retried; a transient transport
  status is retried once by :mod:`scorer_retry`, exactly like every other adapter.
* Privacy: the prompt stays on the machine only when the configured endpoint is local. This
  plugin cannot vouch for a remote URL an operator typed.

Everything here is fail-open: any error returns ``None`` plus a reason code from the table in
:mod:`jev_client`. The transport is injectable so unit tests never open a socket.
"""

from __future__ import annotations

import logging
import math
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from . import rubric
from . import scorer_http
from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://localhost:11434"
CHAT_PATH = "/v1/chat/completions"
#: Letters offered for named choices, in caller-supplied route order (seven levels exist).
CHOICE_LETTERS = "ABCDEFGHIJ"
#: Alternatives requested for the single generated token.
TOP_LOGPROBS = 5


def normalize_endpoint(value: Any) -> str:
    """The configured Ollama base without a trailing slash; blank means the local default."""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_ENDPOINT
    return text.rstrip("/") or DEFAULT_ENDPOINT


def valid_endpoint(value: Any) -> bool:
    """True for an http(s) URL that parses and names a host.

    ``not a url``, a bare host without a scheme, a non-HTTP scheme, a missing host and a
    malformed port are all rejected; the caller reports ``endpoint_invalid``.
    """
    text = normalize_endpoint(value)
    if not text or any(char.isspace() or ord(char) < 32 for char in text):
        return False
    try:
        parts = urllib.parse.urlsplit(text)
        parts.port  # raises ValueError on a malformed or out-of-range port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def chat_url(base: Any) -> str:
    """The OpenAI-compatible chat completions URL for *base*."""
    text = normalize_endpoint(base)
    return text if text.endswith(CHAT_PATH) else text + CHAT_PATH


def answer_key(choices: Optional[Sequence[str]] = None) -> Tuple[Dict[str, Any], str]:
    """``(allowed answer token -> value, kind)`` for a named-choice or numeric-score call."""
    levels = rubric.normalize_effort_choices(choices)
    if levels:
        return dict(zip(CHOICE_LETTERS, levels)), "choice"
    return {"0": 0.0, "1": 1.0, "2": 2.0}, "score"


def _answer_instructions(choices: Optional[Sequence[str]]) -> str:
    """One-sentence output contract matching :func:`answer_key`."""
    levels = rubric.normalize_effort_choices(choices)
    if levels:
        mapping = ", ".join(
            f"{letter} = {level}" for letter, level in zip(CHOICE_LETTERS, levels))
        return f"Answer with exactly one letter and nothing else: {mapping}."
    return "Answer with exactly one digit and nothing else: 0 = low, 1 = medium, 2 = high."


def build_messages(prompt: str, classification_instructions: Any = None,
                   choices: Optional[Sequence[str]] = None,
                   max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS) -> "list[dict[str, str]]":
    """System rubric plus the bounded task text, with an explicit one-token answer contract."""
    question = rubric.questions_for(classification_instructions, choices)["effort"]
    criteria = question["criteria"]
    if isinstance(criteria, dict):
        lines = [f"- {level}: {text}" for level, text in criteria.items()]
    else:
        labels = ("low", "medium", "high")
        digits = ("0", "1", "2")
        lines = [f"- {digit} ({label}): {text}"
                 for digit, label, text in zip(digits, labels, criteria)]
    system = "\n".join([
        str(question["instructions"]),
        "",
        "Criteria:",
        *lines,
        "",
        _answer_instructions(choices),
    ])
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": truncate_prompt(prompt, max_prompt_chars)},
    ]


def _normalize_token(token: Any) -> str:
    """One letter (any case) or one digit, stripped; everything else is unusable."""
    if not isinstance(token, str):
        return ""
    text = token.strip()
    if len(text) != 1:
        return ""
    return text.upper()


def read_answer(payload: Any,
                choices: Optional[Sequence[str]] = None) -> "Tuple[Any, Optional[str]]":
    """Read the highest-logprob allowed answer; anything else is ``malformed_response``."""
    key, _kind = answer_key(choices)
    try:
        top = payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        return None, "malformed_response"
    if not isinstance(top, list) or not top:
        return None, "malformed_response"
    best: Optional[float] = None
    winners: "set[Any]" = set()
    for entry in top:
        if not isinstance(entry, dict):
            continue
        token = _normalize_token(entry.get("token"))
        if token not in key:
            continue
        raw = entry.get("logprob")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        logprob = float(raw)
        if not math.isfinite(logprob):
            continue
        if best is None or logprob > best:
            best, winners = logprob, {key[token]}
        elif logprob == best:
            winners.add(key[token])
    if best is None or len(winners) != 1:
        return None, "malformed_response"
    return winners.pop(), None


class OllamaClient:
    """Ask a local Ollama model for one letter or digit. Never raises out of ``classify``."""

    def __init__(self, *, endpoint: str = DEFAULT_ENDPOINT, model: str = "",
                 timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 classification_instructions: str = "",
                 transport: Optional[Callable[..., Any]] = None):
        self.endpoint = normalize_endpoint(endpoint)
        self.url = chat_url(self.endpoint)
        self.model = str(model or "").strip()
        self.timeout = float(timeout) if timeout else DEFAULT_TIMEOUT_S
        self.max_prompt_chars = int(max_prompt_chars or DEFAULT_MAX_PROMPT_CHARS)
        self.classification_instructions = rubric.normalize_classification_instructions(
            classification_instructions)
        self._transport = transport

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None) -> "Tuple[Any, Optional[str]]":
        """``(label or score, failure)``; ``failure`` is a reason code, never prompt text."""
        try:
            return self._classify_detail(prompt, choices)
        except Exception:
            logger.debug("hermes-adaptive-effort: Ollama classification raised; failing open")
            return None, "unexpected_error"

    def _classify_detail(self, prompt: Optional[str],
                         choices: Optional[Sequence[str]]) -> "Tuple[Any, Optional[str]]":
        if not isinstance(prompt, str) or not prompt.strip():
            return None, "invalid_prompt"
        if not self.model:
            return None, "model_missing"
        if not valid_endpoint(self.endpoint):
            return None, "endpoint_invalid"

        body = {
            "model": self.model,
            "messages": build_messages(
                prompt, self.classification_instructions, choices, self.max_prompt_chars),
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": TOP_LOGPROBS,
            "stream": False,
        }
        request = scorer_http.build_request(
            self.url, body, user_agent=scorer_http.USER_AGENT)
        started = time.monotonic()
        payload, failure = scorer_http.send(
            request, self._transport or scorer_http.default_transport, self.timeout)
        if failure:
            logger.debug("hermes-adaptive-effort: Ollama request failed (%s)", failure)
            return None, failure
        data, failure = scorer_http.read_json(payload)
        if failure:
            return None, failure
        result, failure = read_answer(data, choices)
        logger.debug("hermes-adaptive-effort: Ollama classified in %.0fms (valid=%s, failure=%s)",
                     (time.monotonic() - started) * 1000, result is not None, failure)
        return result, failure

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named level from the local model."""
        return self.classify_detail(prompt, choices=choices)


def credential_present() -> bool:
    """Ollama needs no credential; the status payload shows none and never asks for one."""
    return True
