"""Bounded OpenCode Go System One effort-scoring adapter.

Verified live by the operator on 2026-11-24 against the fixed endpoint below:
POST with a named ``hermes-adaptive-effort/<version>`` User-Agent, a Bearer Go key,
no ``x-opencode-session`` header, and the plugin's exact ``rubric.questions_for()``
body returned finite 0..2 scores and valid named choices for all known decision
models. A generic ``Python-urllib`` User-Agent is rejected by Cloudflare with
HTTP 403 code 1010, so every request uses :data:`scorer_http.USER_AGENT`.

The endpoint is fixed; there is no operator-configurable URL. The shared
``scorer_model`` setting selects the decision model: empty uses
:data:`DEFAULT_MODEL`, and any non-empty id is passed through unchanged as an
operator assertion (the adapter does not verify transport support).

Fail-open like every adapter: a missing credential, timeout, transport error or
malformed answer returns ``None`` and the caller leaves the request untouched.
The transport is injectable so unit tests never open a socket. Hermes core and
its secret scope are imported lazily inside the key reader.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from typing import Any, Callable, Optional, Sequence

from . import rubric
from . import scorer_http
from .jev_client import DEFAULT_MAX_PROMPT_CHARS, DEFAULT_TIMEOUT_S, truncate_prompt

logger = logging.getLogger(__name__)

#: Fixed System One decision route on OpenCode Go (no configurable endpoint).
DEFAULT_ENDPOINT = "https://opencode.ai/inference/go/systemone/v1/systemone"
#: Used when the shared ``scorer_model`` setting is empty.
DEFAULT_MODEL = "jev-1.14"
#: Known decision model ids (informational: a configured non-empty id is passed through).
KNOWN_MODELS = ("jev-1.14", "gemini-decision-1", "gpt-6-luna-decision")
#: Primary credential, resolved through Hermes' secret scope then the environment.
SENTINEL_ENV = "OPENCODE_GO_API_KEY"
#: Legacy credential name, used only when the primary is absent or empty.
LEGACY_SENTINEL_ENV = "OPENCODE_API_KEY"


def _default_key_reader() -> str:
    """Resolve the Go key via primary then legacy name, secret scope before env."""
    return (scorer_http.read_secret(SENTINEL_ENV)
            or scorer_http.read_secret(LEGACY_SENTINEL_ENV))


def _default_transport(request: urllib.request.Request, timeout: float):
    """POST once to the fixed endpoint without following redirects."""
    return scorer_http.default_transport(request, timeout)


class OpenCodeGoClient:
    """Ask OpenCode Go one System One question. Never raises out of ``classify``."""

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
        return scorer_http.resolve_key("opencode_go", _default_key_reader)

    def classify(self, prompt: Optional[str]) -> Optional[float]:
        """Rubric score for *prompt*, or ``None`` (fail-open) on any problem."""
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None, *,
                        continuation: bool = False) -> "tuple[Any, Optional[str]]":
        """``(score, failure)`` — the shared adapter contract.

        ``failure`` is ``None`` when an answer came back, otherwise a reason code
        only (never prompt text, a secret or a provider message):

        ================  ========================================================
        reason            meaning
        ================  ========================================================
        invalid_prompt    nothing usable to classify (empty / not a string)
        credential_missing no Go key resolved, so no request was built
        http_error        the endpoint answered with a non-2xx status
        timeout           the transport timed out
        transport_error   connection / DNS / protocol failure
        malformed_response the answer arrived but carried no valid score/choice
        unexpected_error  anything else (still fail-open)
        ================  ========================================================
        """
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
        # The named product User-Agent is required by this host. No session header:
        # build_request is called without extra_headers.
        request = scorer_http.build_request(
            self.endpoint, body, key=key, user_agent=scorer_http.USER_AGENT)
        started = time.monotonic()
        payload, failure = scorer_http.send(request, self._transport or _default_transport,
                                            self.timeout)
        if failure:
            logger.debug("hermes-adaptive-effort: classification failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, failure
        data, failure = scorer_http.read_json(payload)
        if failure:
            logger.debug("hermes-adaptive-effort: no usable answer (%s, %.0fms)", failure,
                         (time.monotonic() - started) * 1000)
            return None, failure

        levels = rubric.normalize_effort_choices(choices)
        if continuation:
            score, failure = rubric.systemone_noul(data)
        elif levels:
            score, failure = rubric.systemone_choice(data, levels)
        else:
            score, failure = rubric.systemone_score(data)
        logger.debug("hermes-adaptive-effort: classified in %.0fms (valid=%s, failure=%s)",
                     (time.monotonic() - started) * 1000, score is not None, failure)
        return score, failure

    def classify_continuation(self, prompt: Optional[str]):
        """``(probability, failure)`` of one separate yes/no continuation question."""
        return self.classify_detail(prompt, continuation=True)

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named level using a System One Choice question."""
        return self.classify_detail(prompt, choices=choices)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """True when a Go credential resolves.

    Opens nothing, sends nothing and never returns the secret itself — it only
    answers "would a request be built at all?", for ``/hae status``.
    """
    try:
        reader = key_reader or _default_key_reader
        return bool(str(reader() or "").strip())
    except Exception:
        return False
