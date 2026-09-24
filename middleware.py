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


# ── session state ───────────────────────────────────────────────────────────

def reset_state() -> None:
    """Drop every in-memory session decision (tests / plugin reload)."""
    with _lock:
        _SESSIONS.clear()


def session_state() -> Dict[str, Dict[str, Any]]:
    """Snapshot of stored decisions: effort metadata only, never prompt text."""
    with _lock:
        return {k: dict(v) for k, v in _SESSIONS.items()}


def _remember(session_id: str, entry: Dict[str, Any], max_sessions: int) -> None:
    with _lock:
        _SESSIONS[session_id] = entry
        _SESSIONS.move_to_end(session_id)
        while len(_SESSIONS) > max(1, int(max_sessions)):
            _SESSIONS.popitem(last=False)


def on_session_end(session_id: Optional[str] = None, **kwargs: Any) -> None:
    """Verified hook: clears the session's decision (in-memory only)."""
    if not session_id:
        return
    with _lock:
        _SESSIONS.pop(str(session_id), None)


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


def _classify(prompt: str, settings: Dict[str, Any]) -> Optional[float]:
    """Score the prompt; ``None`` on any failure (fail-open)."""
    factory = _classifier_factory
    if factory is None:
        try:
            client = _jev_client.JevClient(timeout=settings["timeout_s"],
                                           endpoint=settings["endpoint"],
                                           max_prompt_chars=settings["prompt_chars"])
        except Exception:
            return None
    else:
        try:
            client = factory(timeout=settings["timeout_s"])
        except Exception:
            return None
    try:
        return client.classify(prompt)
    except Exception:
        logger.debug("jev-auto: classifier raised; failing open")
        return None


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

    entry = _SESSIONS.get(session_id)
    if entry is not None and entry.get("state") in ("failed", "unsupported"):
        # A failed attempt, or a request with nothing writable: stay silent and
        # do not re-classify within this session (bounded Jev usage).
        return None
    slot = _effort_slot(request)
    if slot is None:
        # Nothing verifiable to rewrite: remember and stay silent.
        if entry is None:
            _remember(session_id, {"state": "unsupported"}, settings["max_sessions"])
        logger.debug("jev-auto: no writable effort field; no change")
        return None

    if entry is None or entry.get("state") != "decided":
        prompt = _first_user_text(request.get("messages"))
        if not prompt:
            if entry is None:
                _remember(session_id, {"state": "unsupported"}, settings["max_sessions"])
            return None

        if entry is None:  # first request of this session
            entry = {"state": "probing"}
            _remember(session_id, entry, settings["max_sessions"])

        score = _classify(prompt, settings)
        if score is None:
            entry.update(state="failed")
            return None
        label = _effort.score_to_label(score)
        if label is None:
            entry.update(state="failed")
            return None
        target = _effort.map_effort(label, provider, model)
        if target is None:
            entry.update(state="unsupported")
            return None
        entry.update(state="decided", label=label, target=target)

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
