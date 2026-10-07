"""Bounded Jev classification adapter.

Verified contract (source: installed ``~/.hermes/plugins/jev-approvals`` plugin,
which ships the same transport against the same host):

* Endpoint: ``https://api.typesafe.ai/v1`` + ``/systemone`` (TypeSafe route).
* Request body: ``{"state": {...}, "model": <configured jev_model>, "questions": {...}}``
  (default ``jev-latest``).
* A ``score`` question returns ``{"answers": {"<key>": {"score": <float>}}}``,
  where the score is a weighted position across the ordered rubric criteria.
* Credential: ``TYPESAFE_API_KEY`` (TypeSafe route), resolved the way Hermes
  resolves secrets — core's secret scope first, then the environment.

Everything in this module is fail-open: any transport error, timeout, missing
credential, or malformed answer returns ``None`` and the caller leaves the
request untouched. The transport is injectable so unit tests never open a socket.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional, Sequence

from . import rubric

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"
DEFAULT_ENDPOINT = DEFAULT_BASE_URL + "/systemone"
SENTINEL_ENV = "TYPESAFE_API_KEY"
JEV_MODEL = "jev-latest"
DEFAULT_TIMEOUT_S = 3.0
DEFAULT_MAX_PROMPT_CHARS = 4000

#: The scoring route. ``endpoint`` may name either this full path or the API base
#: that contains it — see :func:`normalize_endpoint`.
SYSTEMONE_PATH = "/systemone"
_VERSION_ROOT = re.compile(r"^v\d+(?:\.\d+)*$")


def normalize_endpoint(value: Any) -> str:
    """Return the URL to POST to, tolerating a base URL in ``endpoint``.

    ``plugins.entries.hermes-adaptive-effort.settings.endpoint`` is written by hand, so both of
    these must land on the scoring route:

    * the full path — ``https://api.typesafe.ai/v1/systemone`` (used verbatim)
    * the API base — ``https://api.typesafe.ai/v1`` → ``…/v1/systemone``
    * a bare host — ``https://api.typesafe.ai`` → ``…/v1/systemone``

    A trailing slash is ignored. Any URL that already carries a non-version path
    is used verbatim, so a deployment behind a proxy with its own route keeps
    working. Without this, a base URL in the setting posts to the API root, which
    answers 404 and makes every classification fail open as ``http_error``.
    """
    url = str(value or "").strip().rstrip("/")
    if not url:
        return DEFAULT_ENDPOINT
    segments = [segment for segment in urllib.parse.urlsplit(url).path.split("/")
                if segment]
    if not segments:
        return url + "/v1" + SYSTEMONE_PATH
    if _VERSION_ROOT.match(segments[-1]):
        return url + SYSTEMONE_PATH
    return url


#: Ordered 3-level rubric: index 0 = low, 1 = medium, 2 = high (see effort.py).
QUESTIONS = rubric.QUESTIONS


def _default_key_reader() -> str:
    """Resolve the TypeSafe credential through Hermes' secret scope, then the env."""
    try:
        from agent.secret_scope import get_secret
        value = get_secret(SENTINEL_ENV, "") or ""
        if str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    import os
    return (os.environ.get(SENTINEL_ENV) or "").strip()


def _default_transport(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


def truncate_prompt(text: str, limit: int) -> str:
    """Head+tail cut with a visible marker; the result never exceeds *limit*."""
    if limit <= 0 or len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit - limit * 2 // 3
    omitted = len(text) - head - tail
    while True:
        marker = f" [... {omitted} chars omitted ...] "
        budget = limit - (head + tail + len(marker))
        if budget >= 0:
            return f"{text[:head]}{marker}{text[-tail:]}" if tail else f"{text[:head]}{marker}"
        # Marker itself overflowed: shrink the kept parts to fit the cap.
        shrink = len(marker) - budget
        head_drop = min(shrink // 2, head)
        tail_drop = min(shrink - head_drop, tail)
        head -= head_drop
        tail -= tail_drop
        omitted += head_drop + tail_drop
        if head + tail == 0:
            return text[:limit]


class JevClient:
    """Ask Jev one score question about a prompt. Never raises out of ``classify``."""

    def __init__(self, *, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT,
                 model: str = JEV_MODEL, timeout: float = DEFAULT_TIMEOUT_S,
                 max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
                 classification_instructions: str = "",
                 transport: Optional[Callable[..., Any]] = None,
                 key_reader: Optional[Callable[[], str]] = None):
        self.api_key = (api_key or "").strip()
        self.endpoint = normalize_endpoint(endpoint or DEFAULT_ENDPOINT)
        self.model = str(model or JEV_MODEL).strip() or JEV_MODEL
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
        """Rubric score for *prompt*, or ``None`` (fail-open) on any problem."""
        return self.classify_detail(prompt)[0]

    def classify_detail(self, prompt: Optional[str],
                        choices: Optional[Sequence[str]] = None) -> "tuple[Any, Optional[str]]":
        """``(score, failure)`` — the same call as :meth:`classify`, plus the reason.

        ``failure`` is ``None`` when a score came back, otherwise one of the
        documented codes below. That is what ``/hae status`` reports, and
        it is a *reason code* only: prompt text never reaches it.

        ================  =======================================================
        reason            meaning
        ================  =======================================================
        invalid_prompt    nothing usable to classify (empty / not a string)
        credential_missing no ``TYPESAFE_API_KEY``, so no request was built
        http_error        the endpoint answered with a non-2xx status
        timeout           the transport timed out
        transport_error   connection / DNS / protocol failure
        malformed_response the answer arrived but carried no valid score
        unexpected_error  anything else (still fail-open)
        ================  =======================================================
        """
        if not isinstance(prompt, str) or not prompt.strip():
            return None, "invalid_prompt"
        key = self._key()
        if not key:
            # No credential: never open a connection at all.
            logger.debug("hermes-adaptive-effort: no %s credential; skipping classification", SENTINEL_ENV)
            return None, "credential_missing"
        body = {
            "state": {"prompt": truncate_prompt(prompt, self.max_prompt_chars)},
            "model": self.model,
            "questions": rubric.questions_for(self.classification_instructions, choices),
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "hermes-hermes-adaptive-effort/0.1",
            },
            method="POST",
        )
        started = time.monotonic()
        transport = self._transport or _default_transport
        try:
            payload = transport(request, self.timeout)
        except urllib.error.HTTPError:
            logger.debug("hermes-adaptive-effort: classification failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "http_error"
        except urllib.error.URLError as exc:
            reason = "timeout" if isinstance(getattr(exc, "reason", None), TimeoutError) \
                else "transport_error"
            logger.debug("hermes-adaptive-effort: classification failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, reason
        except TimeoutError:
            logger.debug("hermes-adaptive-effort: classification failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "timeout"
        except OSError:
            logger.debug("hermes-adaptive-effort: classification failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "transport_error"
        except Exception:
            logger.debug("hermes-adaptive-effort: classification failed after %.0fms",
                         (time.monotonic() - started) * 1000)
            return None, "unexpected_error"

        status = getattr(payload, "status", None)
        if isinstance(status, int) and not 200 <= status < 300:
            logger.debug("hermes-adaptive-effort: endpoint returned HTTP %d", status)
            return None, "http_error"

        try:
            if hasattr(payload, "__enter__"):
                with payload as resp:
                    data = json.load(resp) if hasattr(resp, "read") else resp
            elif hasattr(payload, "read"):
                data = json.load(payload)
            else:
                data = payload
        except Exception:
            logger.debug("hermes-adaptive-effort: answer was not JSON (%.0fms)",
                         (time.monotonic() - started) * 1000)
            return None, "malformed_response"

        levels = rubric.normalize_effort_choices(choices)
        if levels:
            score, failure = rubric.systemone_choice(data, levels)
        else:
            score, failure = _extract_score_detail(data)
        logger.debug("hermes-adaptive-effort: classified in %.0fms (valid=%s, failure=%s)",
                     (time.monotonic() - started) * 1000, score is not None, failure)
        return score, failure

    def classify_effort_detail(self, prompt: Optional[str], choices: Sequence[str]):
        """Return one route-authorized named level using a System One Choice question."""
        return self.classify_detail(prompt, choices=choices)


def credential_present(key_reader: Optional[Callable[[], str]] = None) -> bool:
    """True when a Jev credential resolves.

    Opens nothing, sends nothing and never returns the secret itself — it only
    answers "would a request be built at all?", for ``/hae status``.
    """
    try:
        reader = key_reader or _default_key_reader
        return bool(str(reader() or "").strip())
    except Exception:
        return False


def _extract_score(payload: Any) -> Optional[float]:
    """Read ``answers.effort.score`` strictly; anything unexpected is ``None``."""
    return _extract_score_detail(payload)[0]


def _extract_score_detail(payload: Any) -> "tuple[Optional[float], Optional[str]]":
    """``(score, failure)`` for a parsed answer; ``failure`` is a reason code."""
    return rubric.systemone_score(payload)
