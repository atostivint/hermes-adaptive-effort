"""``llm_request`` middleware: classify once per session, rewrite only a verified field.

Contract (verified against ``hermes_cli/middleware.py`` and
``agent/turn_api_request.py``):

* Register with ``ctx.register_middleware("llm_request", callback)``.
* The callback receives keyword arguments: the ``request`` payload plus a context
  that carries ``session_id``, ``task_id``, ``turn_id``, ``api_request_id``,
  ``provider``, ``model``, ``api_mode`` and schema versions. Extra keys are
  tolerated — unknown kwargs are dropped.
* Return ``{"request": <full replacement>, "source": ..., "reason": ...}`` to
  change the request, or ``None`` to leave it byte-for-byte untouched.
* Exceptions escape as a failed middleware attempt; the request survives without
  a rewrite. This module therefore catches everything itself and fails open.
* Trace entries are recorded as ``middleware_trace`` on the request. Our reason
  strings carry effort values only — never prompt text.

Scope rules enforced here: default mode is off; only an *existing* effort field
is ever rewritten (no field is invented, thinking is never re-enabled); the new
value is clamped onto the route's declared vocabulary; one Jev call per session
(and none at all when the field cannot be rewritten); no prompt text is stored.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from . import effort as _effort
from . import jev_client as _jev_client

logger = logging.getLogger(__name__)

PLUGIN_ID = "jev-auto"
VALID_MODES: Tuple[str, ...] = ("off", "recommend", "auto")
DEFAULTS: Dict[str, Any] = {
    "mode": "off",
    "timeout_s": _jev_client.DEFAULT_TIMEOUT_S,
    "max_sessions": 64,
    "prompt_chars": _jev_client.DEFAULT_MAX_PROMPT_CHARS,
    "endpoint": _jev_client.DEFAULT_ENDPOINT,
}

# Injected by register(ctx); None until a PluginContext exists (and in tests).
_settings_provider: Optional[Callable[[str, Any], Any]] = None
# Injected by tests so classification never reaches the network.
_classifier_factory: Optional[Callable[..., Any]] = None

_lock = threading.Lock()
_SESSIONS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
#: Sessions with a classification running right now — at most one probe per
#: session, claimed atomically so two concurrent requests cannot both call Jev.
_IN_FLIGHT: set = set()


# ── session state ───────────────────────────────────────────────────────────

def reset_state() -> None:
    """Drop every in-memory session decision (tests / plugin reload)."""
    with _lock:
        _SESSIONS.clear()
        _IN_FLIGHT.clear()


def session_state() -> Dict[str, Dict[str, Any]]:
    """Snapshot of stored decisions: effort metadata only, never prompt text."""
    with _lock:
        return {k: dict(v) for k, v in _SESSIONS.items()}


def in_flight() -> Tuple[str, ...]:
    """Session ids whose probe is running right now (sorted snapshot)."""
    with _lock:
        return tuple(sorted(_IN_FLIGHT))


def _claim(session_id: str) -> bool:
    """Atomically take the single probe slot for *session_id*.

    ``True`` when this caller owns the probe, ``False`` when another caller is
    already probing the same session — the loser fails open instead of making a
    second Jev call.
    """
    with _lock:
        if session_id in _IN_FLIGHT:
            return False
        _IN_FLIGHT.add(session_id)
        return True


def _release(session_id: str) -> None:
    """Give the probe slot back (idempotent; always called from ``finally``)."""
    with _lock:
        _IN_FLIGHT.discard(session_id)


def _remember(session_id: str, entry: Dict[str, Any], max_sessions: int) -> None:
    with _lock:
        _SESSIONS[session_id] = entry
        _SESSIONS.move_to_end(session_id)
        while len(_SESSIONS) > max(1, int(max_sessions)):
            _SESSIONS.popitem(last=False)


def _touch(session_id: str, settings: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """The session's record: created once, then counted and stamped here.

    Every request that reaches the decision path is counted (``requests``), while
    ``probes`` only ever grows inside the claim — that difference is exactly what
    ``/jev-auto status`` reports as "one Jev call per session".
    """
    with _lock:
        entry = _SESSIONS.get(session_id)
        if entry is None:
            entry = {
                "state": "new", "label": None, "target": None, "score": None,
                "mode": mode, "requests": 0, "probes": 0, "elapsed_ms": 0.0,
                "failure": None, "updated_at": 0.0,
            }
            _SESSIONS[session_id] = entry
        entry["requests"] = int(entry.get("requests") or 0) + 1
        entry["mode"] = mode
        entry["updated_at"] = time.time()
        _SESSIONS.move_to_end(session_id)
        while len(_SESSIONS) > max(1, int(settings["max_sessions"])):
            _SESSIONS.popitem(last=False)
    return entry


def on_session_end(session_id: Optional[str] = None, **kwargs: Any) -> None:
    """Verified hook: clears the session's decision (in-memory only)."""
    if not session_id:
        return
    with _lock:
        _SESSIONS.pop(str(session_id), None)
        _IN_FLIGHT.discard(str(session_id))


# ── settings ────────────────────────────────────────────────────────────────

def _read_setting(key: str, default: Any = None) -> Any:
    """``plugins.entries.jev-auto.settings.<key>``, via ctx when present."""
    provider = _settings_provider
    if provider is not None:
        try:
            value = provider(key, default)
            return default if value in (None, "") else value
        except Exception:
            return default
    try:
        from hermes_cli.config import load_config_readonly
        entry = (((load_config_readonly() or {}).get("plugins") or {})
                 .get("entries") or {}).get(PLUGIN_ID) or {}
        value = (entry.get("settings") or {}).get(key)
        return default if value in (None, "") else value
    except Exception:
        return default


def _settings() -> Dict[str, Any]:
    mode = str(_read_setting("mode", DEFAULTS["mode"]) or "").strip().lower()
    if mode not in VALID_MODES:
        mode = "off"

    def _num(key: str, fallback: float) -> float:
        try:
            value = float(_read_setting(key, fallback))
            return value if value > 0 else float(fallback)
        except Exception:
            return float(fallback)

    def _int(key: str, fallback: int) -> int:
        try:
            value = int(float(_read_setting(key, fallback)))
            return value if value > 0 else int(fallback)
        except Exception:
            return int(fallback)

    return {
        "mode": mode,
        "timeout_s": _num("timeout_s", DEFAULTS["timeout_s"]),
        "max_sessions": _int("max_sessions", DEFAULTS["max_sessions"]),
        "prompt_chars": _int("prompt_chars", DEFAULTS["prompt_chars"]),
        "endpoint": str(_read_setting("endpoint", DEFAULTS["endpoint"])),
    }


def _classifies(client: Any) -> bool:
    """True when *client* exposes the classification surface we drive."""
    return (callable(getattr(client, "classify", None))
            or callable(getattr(client, "classify_detail", None)))


def _call_factory(factory: Any, timeout: Any) -> Any:
    """``factory(timeout=…)`` with a plain ``factory()`` fallback.

    Every useful factory accepts the configured timeout; a bare class handed
    where an instance was meant rejects keyword arguments outright, so that call
    is retried once without them. Both paths fail open and never raise.
    """
    try:
        return factory(timeout=timeout)
    except TypeError:
        try:
            return factory()
        except Exception:
            logger.debug("jev-auto: classifier factory raised; failing open",
                         exc_info=True)
            return None
    except Exception:
        logger.debug("jev-auto: classifier factory raised; failing open", exc_info=True)
        return None


def _build_client(factory: Any, settings: Dict[str, Any]) -> Any:
    """Resolve *factory* to something that can classify, or ``None``.

    Accepted shapes: a function/class returning a client, an instance whose
    ``__call__`` returns one, and a factory that returns *another* factory (a
    bare class, which must be instantiated before it classifies). At most three
    layers are unwrapped; anything else fails open.
    """
    candidate: Any = factory
    for _ in range(3):
        if not callable(candidate):
            return None
        built = _call_factory(candidate, settings["timeout_s"])
        if built is None:
            return None
        if _classifies(built) or not callable(built):
            return built
        candidate = built
    return None


def _classify(prompt: str, settings: Dict[str, Any]) -> "Tuple[Optional[float], Optional[str]]":
    """``(score, failure)`` for one classification.

    ``failure`` is ``None`` when a score came back, otherwise a reason code from
    the documented table in :mod:`jev_client` plus ``classifier_error`` (the
    injected client raised, was unavailable, or answered nothing at all). Every
    path fails open — this function never raises.
    """
    factory = _classifier_factory
    if factory is None:
        try:
            client = _jev_client.JevClient(timeout=settings["timeout_s"],
                                           endpoint=settings["endpoint"],
                                           max_prompt_chars=settings["prompt_chars"])
        except Exception:
            logger.debug("jev-auto: JevClient unavailable; failing open", exc_info=True)
            return None, "classifier_error"
    else:
        client = _build_client(factory, settings)
        if client is None:
            return None, "classifier_error"
    detail: Any = getattr(client, "classify_detail", None)
    try:
        if callable(detail):
            score, failure = detail(prompt)
        else:
            score, failure = client.classify(prompt), None
    except Exception:
        logger.debug("jev-auto: classifier raised; failing open", exc_info=True)
        return None, "classifier_error"
    if score is None:
        return None, failure or "classifier_error"
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None, "malformed_response"
    return score, None


def run_probe(prompt: str,
              settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Exactly one bounded classification for ``/jev-auto probe``.

    Deliberately touches **no** session state and claims no in-flight slot: a
    probe is an operator's question about a text they typed themselves, not a
    request decision.
    """
    settings = settings if settings is not None else _settings()
    started = time.monotonic()
    if isinstance(prompt, str) and prompt.strip():
        score, failure = _classify(prompt, settings)
    else:
        score, failure = None, "invalid_prompt"
    label = _effort.score_to_label(score) if score is not None else None
    if score is None:
        failure = failure or "classifier_error"
    elif label is None:
        failure = "malformed_response"  # a score the rubric cannot express
    else:
        failure = None
    return {
        "score": score,
        "label": label,
        "failure": failure,
        "elapsed_ms": (time.monotonic() - started) * 1000.0,
    }


# ── request inspection ──────────────────────────────────────────────────────

def _first_user_text(messages: Any) -> Optional[str]:
    """First user message of the session — the plan's classification target."""
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [block.get("text", "") for block in content
                     if isinstance(block, dict) and isinstance(block.get("text"), str)]
            text = "".join(parts)
            if text.strip():
                return text
    return None


def _effort_slot(request: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """Locate an *existing* effort field to rewrite: ``(container, key, old)``.

    Only two shapes are treated as authoritative, because both are the shapes
    Hermes itself writes (``agent/transports/chat_completions.py``):
    ``extra_body.reasoning.effort`` and top-level ``reasoning_effort``. An
    absent field, an explicit ``"none"`` level, or ``enabled: false`` is not an
    invitation to turn reasoning on: we refuse and report ``unsupported``.
    """
    extra_body = request.get("extra_body")
    if isinstance(extra_body, dict):
        reasoning = extra_body.get("reasoning")
        if isinstance(reasoning, dict):
            value = reasoning.get("effort")
            if (isinstance(value, str) and value.strip()
                    and value.strip().lower() != "none"
                    and reasoning.get("enabled") is not False):
                return reasoning, "effort", value.strip().lower()
            return None
    value = request.get("reasoning_effort")
    if isinstance(value, str) and value.strip() and value.strip().lower() != "none":
        return request, "reasoning_effort", value.strip().lower()
    return None


def _apply(request: Dict[str, Any], slot: Tuple[Dict[str, Any], str, str],
           target: str) -> Dict[str, Any]:
    """Full replacement payload with exactly one value changed."""
    container, key, old = slot
    if target == old:
        return request
    new = dict(request)
    if container is request:
        new[key] = target
        return new
    extra_body = dict(request.get("extra_body") or {})
    reasoning = dict(extra_body.get("reasoning") or {})
    reasoning[key] = target
    extra_body["reasoning"] = reasoning
    new["extra_body"] = extra_body
    return new


# ── the middleware itself ───────────────────────────────────────────────────

def on_llm_request(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Fail-open: returns ``None`` for every path that must not change a request."""
    try:
        return _handle(kwargs)
    except Exception:
        logger.debug("jev-auto: middleware error; failing open", exc_info=True)
        return None


def _handle(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request = kwargs.get("request")
    if not isinstance(request, dict):
        return None

    settings = _settings()
    mode = settings["mode"]
    if mode == "off":
        return None  # byte-for-byte untouched: no state, no classification

    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        logger.debug("jev-auto: no session id; failing open")
        return None

    provider = kwargs.get("provider")
    model = kwargs.get("model")

    entry = _touch(session_id, settings, mode)
    if entry.get("state") in ("failed", "unsupported"):
        # A failed attempt, or a request with nothing writable: stay silent and
        # do not re-classify within this session (bounded Jev usage).
        return None
    slot = _effort_slot(request)
    if slot is None:
        # Nothing verifiable to rewrite: remember and stay silent.
        if entry.get("state") != "decided":
            entry["state"] = "unsupported"
        logger.debug("jev-auto: no writable effort field; no change")
        return None

    if entry.get("state") != "decided":
        prompt = _first_user_text(request.get("messages"))
        if not prompt:
            entry["state"] = "unsupported"
            return None
        if not _claim(session_id):
            # Another request of this same session is classifying right now:
            # never a second Jev call, and its entry stays untouched.
            logger.debug("jev-auto: probe already in flight; failing open")
            return None
        try:
            entry["state"] = "probing"
            started = time.monotonic()
            score, failure = _classify(prompt, settings)
            entry["elapsed_ms"] = (time.monotonic() - started) * 1000.0
            entry["probes"] = int(entry.get("probes") or 0) + 1
            entry["score"] = score
            entry["failure"] = None if score is not None else failure
            entry["updated_at"] = time.time()
            if score is None:
                entry["state"] = "failed"
                return None
            label = _effort.score_to_label(score)
            if label is None:
                # A number the rubric cannot express: a malformed answer.
                entry["state"] = "failed"
                entry["failure"] = "malformed_response"
                return None
            entry["label"] = label
            target = _effort.map_effort(label, provider, model)
            if target is None:
                entry["state"] = "unsupported"
                return None
            entry["target"] = target
            entry["state"] = "decided"
        finally:
            _release(session_id)

    target = entry.get("target")
    if not isinstance(target, str):
        return None
    if mode == "recommend":
        return {
            "request": request,
            "source": PLUGIN_ID,
            "reason": f"recommend {entry.get('label')} (not applied; wire effort stays "
                      f"'{slot[2]}')",
        }

    new_request = _apply(request, slot, target)
    if new_request is request:
        return None
    return {
        "request": new_request,
        "source": PLUGIN_ID,
        "reason": f"auto {slot[2]} -> {target} ({entry.get('label')})",
    }
