"""``llm_request`` middleware: classify per mode and rewrite only a verified field.

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

Scope rules enforced here: exactly four canonical modes (``auto``, ``once``,
``always``, ``off``); off mode may retain bounded route metadata but never scores
or changes a request; persistent decisions are per session and exact route;
per-turn decisions are reused throughout a tool loop; fields are added only on
explicitly eligible routes; the value is clamped to the route vocabulary; no
prompt text is stored.
"""

from __future__ import annotations

import logging
import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, Optional, Tuple

from . import cache_safety as _cache_safety
from . import effort as _effort
from . import history_store as _history_store
from . import jev_client as _jev_client
from . import model_profiles as _model_profiles
from . import rubric as _rubric
from . import scorers as _scorers

logger = logging.getLogger(__name__)

PLUGIN_ID = "hermes-adaptive-effort"
VALID_MODES: Tuple[str, ...] = ("auto", "once", "always", "off")
DEFAULTS: Dict[str, Any] = {
    "mode": "off",
    "subagent_mode": "off",
    "timeout_s": _jev_client.DEFAULT_TIMEOUT_S,
    "max_turns": 64,
    "prompt_chars": _jev_client.DEFAULT_MAX_PROMPT_CHARS,
    "effort_models": "",
    "endpoint": _jev_client.DEFAULT_ENDPOINT,
    "scorer_provider": _scorers.JEV,
    "jev_model": _jev_client.JEV_MODEL,
    "scorer_model": "",
    "custom_endpoint": "",
    "custom_api_format": "systemone",
    "custom_auth": "none",
    "cloudflare_account_id": "",
    "cloudflare_model": _scorers.cloudflare_client.DEFAULT_MODEL_SELECTOR,
    "classification_instructions": "",
    "use_target_model_context": False,
    "show_tui_status": True,
    "show_desktop_popup": True,
}

# Injected by register(ctx); None until a PluginContext exists (and in tests).
_settings_provider: Optional[Callable[[str, Any], Any]] = None
# Injected by tests so classification never reaches the network.
_classifier_factory: Optional[Callable[..., Any]] = None
# Config source seam. ``None`` in production: the plugin reads the operator's
# live profile. Tests inject a hermetic reader so a unit run never depends on
# (and never reads) whatever mode the live profile happens to carry.
_config_reader: Optional[Callable[[], Dict[str, Any]]] = None
# Mode chosen at runtime through ``/hae auto|once|always|off``.
# Process-local by design: the plugin never writes the operator's config file,
# and the command's reply says so. ``None`` means "use the configured mode".
_MODE_OVERRIDE: Optional[str] = None

_lock = threading.Lock()
_SESSIONS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
# Persistent decisions are keyed by session plus normalized provider, exact model,
# and API mode. Kept separately so each registry has its own max_turns bound.
_PINNED: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
# Claude per-message effort continuity stores only effort labels and private
# hashes/ordinals for user-message positions; never message text.
_CLAUDE_HISTORY: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
# Recent effort rewrites are kept in memory for the Desktop event poller. The
# private decision key deduplicates repeated tool-loop rewrites without ever
# leaving this module.
_CHANGE_HISTORY_LIMIT = 64
_CHANGE_STREAM_ID = uuid.uuid4().hex
_CHANGE_SEQUENCE = 0
_EFFORT_CHANGES: "deque[Dict[str, Any]]" = deque(maxlen=_CHANGE_HISTORY_LIMIT)
_DESKTOP_EVENT_REVISION = 0
_CLI_STATUS_HANDLE: Any = None
_CLI_STATUS_TEXT = "Effort: N/A"
#: Sessions with a classification running right now — at most one probe per
#: session, claimed atomically so two concurrent requests cannot both call the scorer.
_IN_FLIGHT: set = set()
# Persistent route claims coordinate `once`/fallback decisions across turns.
# They stay separate from the public per-turn in-flight snapshot.
_ROUTE_IN_FLIGHT: set = set()
#: child_session_id -> the goal its parent wrote. Populated by the verified
#: ``subagent_start`` hook, which hands over the text verbatim, so the classifier
#: never parses the child's prompt. A child is its own agent with its own
#: session_id, which is the only reliable way to tell a child's requests apart.
_CHILD_GOALS: "OrderedDict[str, str]" = OrderedDict()


# ── session state ───────────────────────────────────────────────────────────

def reset_state() -> None:
    """Drop every in-memory session decision (tests / plugin reload).

    Also drops a runtime mode override: a reset means "back to the configured
    mode", so no test can leak a mode into the next one.
    """
    global _MODE_OVERRIDE, _CHANGE_SEQUENCE, _CHANGE_STREAM_ID, _CLI_STATUS_HANDLE
    global _DESKTOP_EVENT_REVISION
    global _CLI_STATUS_TEXT
    with _lock:
        _SESSIONS.clear()
        _PINNED.clear()
        _CLAUDE_HISTORY.clear()
        _IN_FLIGHT.clear()
        _ROUTE_IN_FLIGHT.clear()
        _CHILD_GOALS.clear()
        _EFFORT_CHANGES.clear()
        _CHANGE_SEQUENCE = 0
        _CHANGE_STREAM_ID = uuid.uuid4().hex
        _DESKTOP_EVENT_REVISION = 0
        _CLI_STATUS_HANDLE = None
        _CLI_STATUS_TEXT = "Effort: N/A"
        _MODE_OVERRIDE = None


# ── runtime mode override (``/hae auto|once|always|off``) ────────────────────

def set_mode_override(mode: Any) -> Optional[str]:
    """Accept a runtime mode for FUTURE requests; ``None`` when it is not a mode.

    The value is kept for this process only. Writing it to the operator's config
    would make a chat command mutate a configuration file the operator owns —
    that decision belongs to the operator, so the command explains the scope
    instead of persisting behind their back.
    """
    value = str(mode or "").strip().lower()
    if value not in VALID_MODES:
        return None
    global _MODE_OVERRIDE
    with _lock:
        _MODE_OVERRIDE = value
    return value


def clear_mode_override() -> None:
    """Forget the runtime override; the configured mode applies again."""
    global _MODE_OVERRIDE
    with _lock:
        _MODE_OVERRIDE = None


def mode_override() -> Optional[str]:
    """The runtime override in force, or ``None`` when the config decides."""
    with _lock:
        return _MODE_OVERRIDE


def _decision_key(session_id: str, turn_id: Any) -> str:
    """Memo key for one turn of one session.

    ``turn_id`` is minted fresh per user message (``agent/turn_context.py``,
    ``_bind_turn_identity``) and is constant for every API request of that turn,
    so keying on it re-classifies each user message while a tool loop inside one
    turn still reuses a single decision — and therefore a single scorer call.
    """
    turn = str(turn_id or "").strip()
    return f"{session_id}/{turn}" if turn else str(session_id)


def _route_identity(provider: Any, model: Any, api_mode: Any) -> Tuple[str, str, str]:
    """Stable identity for one routed model, including aliases of known providers."""
    provider_name = str(provider or "").strip().lower()
    for canonical, aliases in (
        ("opencode-go", {"opencode-go", "opencode_go", "go", "opencode-go-sub"}),
        ("opencode-zen", {"opencode", "opencode-zen", "opencode_zen", "zen"}),
    ):
        if provider_name in aliases:
            provider_name = canonical
            break
    return (provider_name, str(model or "").strip(),
            str(api_mode or "").strip().lower())


def _pin_key(session_id: str, route: Tuple[str, str, str]) -> str:
    """Collision-safe key for a session-and-route decision."""
    import json
    return f"{session_id}/@route/{json.dumps(route, separators=(',', ':'))}"


def _session_of(key: str) -> str:
    """The session a decision key belongs to (used by per-session cleanup)."""
    return key.split("/", 1)[0]


def session_state() -> Dict[str, Dict[str, Any]]:
    """Snapshot of stored decisions: effort metadata only, never prompt text."""
    with _lock:
        return {k: dict(v) for k, v in (*_SESSIONS.items(), *_PINNED.items())}


def effort_change_state() -> Dict[str, Any]:
    """A bounded, prompt-free snapshot for CLI/desktop effort indicators."""
    with _lock:
        events = [
            {key: event[key] for key in ("id", "from", "to", "at")}
            for event in _EFFORT_CHANGES
        ]
        return {
            "stream_id": _CHANGE_STREAM_ID,
            "events": events,
            "latest": dict(events[-1]) if events else None,
        }


def _cache_verdict(provider: Any, model: Any, api_mode: Any,
                   details: Optional[Dict[str, Any]] = None) -> str:
    """Report whether this request transport keeps effort out of the cached prompt."""
    api = str(api_mode or "").strip().lower()
    behavior = details.get("cache_behavior") if isinstance(details, dict) else None
    if behavior in ("per_message", "route_pinned"):
        return "compatible"
    if behavior in ("top_level_cache_may_reset", "invalidated"):
        return "sensitive"
    if api in _cache_safety.CACHE_UNSAFE_API_MODES:
        return "sensitive"
    if _cache_safety.effort_is_cache_safe(provider, model, api_mode):
        return "compatible"
    return "not_verified"


def _history_identity(session_id: Any) -> Tuple[Optional[str], Tuple[str, ...]]:
    scope, aliases = _history_store.current_identity()
    sid = str(session_id or "").strip()
    if scope and aliases:
        return scope, tuple(dict.fromkeys((*aliases, sid)))
    return (sid or None), ((sid,) if sid else ())


def set_history_path_provider(provider: Optional[Callable[[], Any]]) -> None:
    """Bind the journal to the host's profile-owned plugin data directory."""
    _history_store.configure_path_provider(provider)


def set_cli_status_handle(handle: Any) -> None:
    """Attach the optional host-provided CLI status item update handle."""
    global _CLI_STATUS_HANDLE
    _CLI_STATUS_HANDLE = handle
    _sync_cli_status()


def _record_effort_change(decision_key: str, before: Any, after: Any,
                           session_id: Any = None, provider: Any = None,
                           model: Any = None, api_mode: Any = None,
                           details: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Record one distinct rewrite per decision and value pair; return its public event."""
    global _CHANGE_SEQUENCE
    old_value, new_value = str(before), str(after)
    with _lock:
        for event in reversed(_EFFORT_CHANGES):
            if (event.get("_decision_key") == decision_key
                    and event.get("from") == old_value
                    and event.get("to") == new_value):
                return None
        _CHANGE_SEQUENCE += 1
        event = {
            "id": _CHANGE_SEQUENCE,
            "from": old_value,
            "to": new_value,
            "at": time.time(),
            "_decision_key": decision_key,
        }
        _EFFORT_CHANGES.append(event)
    scope, aliases = _history_identity(session_id)
    if scope:
        _history_store.record_change(
            scope, aliases, decision_key, old_value, new_value,
            provider, model, api_mode,
            _cache_verdict(provider, model, api_mode, details=details),
            details=details, at=event["at"],
        )
    return {key: event[key] for key in ("id", "from", "to", "at")}


def _existing_effort_change(decision_key: str, before: Any,
                            after: Any) -> Optional[Dict[str, Any]]:
    """Find the stable applied marker for an already-recorded tool-loop rewrite."""
    old_value, new_value = str(before), str(after)
    with _lock:
        for event in reversed(_EFFORT_CHANGES):
            if (event.get("_decision_key") == decision_key
                    and event.get("from") == old_value
                    and event.get("to") == new_value):
                return {key: event[key] for key in ("id", "from", "to", "at")}
    return None


def _publish_desktop_decision(session_id: str, provider: Any = None,
                              model: Any = None, api_mode: Any = None,
                              entry: Optional[Dict[str, Any]] = None,
                              applied: Optional[Dict[str, Any]] = None,
                              clear: bool = False) -> None:
    """Broadcast a bounded, prompt-free snapshot through Hermes' public event API.

    The import is intentionally lazy: the plugin still loads in CLI/test hosts that
    do not provide the Desktop event bridge. Event failures never affect middleware.
    """
    global _DESKTOP_EVENT_REVISION
    conversation_id = str(session_id or "")
    if not conversation_id:
        return
    try:
        # Reuse the status surface's explicit field allowlist; never serialize the
        # mutable internal decision record or request kwargs wholesale.
        if entry is None or clear:
            status = None
        else:
            from .command import _ENTRY_FIELDS
            status = {field: entry.get(field) for field in _ENTRY_FIELDS}

        if status is not None:
            scope, aliases = _history_identity(conversation_id)
            if scope:
                history_snapshot = dict(status)
                history_snapshot["cache_verdict"] = _cache_verdict(
                    provider, model, api_mode, details=history_snapshot)
                _history_store.record_snapshot(
                    scope, aliases, history_snapshot,
                    at=float(status.get("updated_at") or time.time()),
                )

        def route_value(value: Any) -> str:
            return value if isinstance(value, str) else ""

        with _lock:
            _DESKTOP_EVENT_REVISION += 1
            payload = {
                "schema": "hermes-adaptive-effort.desktop-status.v2",
                "stream_id": _CHANGE_STREAM_ID,
                "revision": _DESKTOP_EVENT_REVISION,
                "conversation_id": conversation_id,
                # Compatibility alias: this has always carried agent.session_id
                # (the stored id), never the Desktop gateway's temporary sid.
                "runtime_session_id": conversation_id,
                "status": status,
                "route": {
                    "provider": route_value(provider),
                    "model": route_value(model),
                    "api_mode": route_value(api_mode),
                },
                "selector_sync_supported": (
                    os.environ.get("HERMES_COMPUTE_HOST_CHILD") != "1"
                    and not (entry and entry.get("cache_behavior") == "per_message")),
                "applied": (dict(applied) if applied is not None else None),
            }
            if clear:
                payload["clear"] = True
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event(PLUGIN_ID, "decision.updated", payload)
    except Exception:
        # The event bridge is a best-effort Desktop surface. Never fail a request
        # or prevent a genuine session boundary when it is unavailable.
        logger.debug("hermes-adaptive-effort: Desktop decision event failed", exc_info=True)


def _sync_cli_status(settings: Optional[Dict[str, Any]] = None) -> None:
    """Render the current status text only when its TUI display is enabled."""
    try:
        handle = _CLI_STATUS_HANDLE
        update = getattr(handle, "update", None)
        if not callable(update):
            return
        current = settings if settings is not None else _settings()
        update(_CLI_STATUS_TEXT if current["show_tui_status"] else "")
    except Exception:
        logger.debug("hermes-adaptive-effort: CLI status item update failed", exc_info=True)


def _update_cli_status(value: str) -> None:
    global _CLI_STATUS_TEXT
    _CLI_STATUS_TEXT = value
    _sync_cli_status()


def _notify_cli(text: str) -> None:
    try:
        notify = getattr(_CLI_STATUS_HANDLE, "notify", None)
        if callable(notify):
            notify(text)
    except Exception:
        logger.debug("hermes-adaptive-effort: CLI notice failed", exc_info=True)


def child_goals() -> Dict[str, str]:
    """Snapshot of the child registry: session id -> the goal its parent wrote."""
    with _lock:
        return dict(_CHILD_GOALS)


def in_flight() -> Tuple[str, ...]:
    """Session ids whose probe is running right now (sorted snapshot)."""
    with _lock:
        return tuple(sorted(_IN_FLIGHT))


def _claim(session_id: str) -> bool:
    """Atomically take the single probe slot for *session_id*.

    ``True`` when this caller owns the probe, ``False`` when another caller is
    already probing the same session — the loser fails open instead of making a
    second scorer call.
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


def _claim_scoring(turn_key: str, route_key: str, persistent_scope: bool,
                   serialize_route: bool = False) -> bool:
    """Atomically claim the turn and, when applicable, the retained route."""
    with _lock:
        if turn_key in _IN_FLIGHT:
            return False
        route_claim = persistent_scope or serialize_route
        if route_claim and route_key in _ROUTE_IN_FLIGHT:
            return False
        _IN_FLIGHT.add(turn_key)
        if route_claim:
            _ROUTE_IN_FLIGHT.add(route_key)
        return True


def _release_scoring(turn_key: str, route_key: str, persistent_scope: bool,
                     serialize_route: bool = False) -> None:
    with _lock:
        _IN_FLIGHT.discard(turn_key)
        if persistent_scope or serialize_route:
            _ROUTE_IN_FLIGHT.discard(route_key)


def _remember(session_id: str, entry: Dict[str, Any], max_sessions: int) -> None:
    with _lock:
        _SESSIONS[session_id] = entry
        _SESSIONS.move_to_end(session_id)
        while len(_SESSIONS) > max(1, int(max_sessions)):
            _SESSIONS.popitem(last=False)


def _touch(key: str, settings: Dict[str, Any], mode: str,
           provider: Any = None, model: Any = None, api_mode: Any = None,
           conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """One turn's record: created once, then counted and stamped here.

    Every request that reaches the decision path is counted (``requests``), while
    ``probes`` only ever grows inside the claim — that difference is exactly what
    ``/hae status`` reports as "one scorer call per turn".
    """
    with _lock:
        entry = _SESSIONS.get(key)
        if entry is None:
            entry = {
                "state": "new", "label": None, "target": None, "score": None,
                "decision_type": None, "choices": [], "cache_behavior": None,
                "mode": mode, "provider": provider, "model": model,
                "scope": "turn",
                "scorer_provider": settings.get("scorer_provider", _scorers.JEV),
                "scorer_model": settings.get("scorer_model_effective", ""),
                "conversation_id": conversation_id,
                "requests": 0, "probes": 0, "elapsed_ms": 0.0,
                "failure": None, "updated_at": 0.0,
            }
            _SESSIONS[key] = entry
        if conversation_id is not None:
            entry["conversation_id"] = conversation_id
        entry["requests"] = int(entry.get("requests") or 0) + 1
        entry["mode"] = mode
        entry["api_mode"] = api_mode
        entry["updated_at"] = time.time()
        _SESSIONS.move_to_end(key)
        while len(_SESSIONS) > max(1, int(settings["max_turns"])):
            _SESSIONS.popitem(last=False)
    return entry


def _touch_pin(key: str, settings: Dict[str, Any], mode: str,
               provider: Any, model: Any, api_mode: Any,
               conversation_id: str) -> Dict[str, Any]:
    """Create/update one bounded persistent route decision."""
    with _lock:
        entry = _PINNED.get(key)
        if entry is None:
            entry = {
                "state": "new", "label": None, "target": None, "score": None,
                "decision_type": None, "choices": [], "cache_behavior": None,
                "mode": mode, "provider": provider, "model": model,
                "scope": "session_route",
                "api_mode": api_mode,
                "scorer_provider": settings.get("scorer_provider", _scorers.JEV),
                "scorer_model": settings.get("scorer_model_effective", ""),
                "conversation_id": conversation_id,
                "requests": 0, "probes": 0, "elapsed_ms": 0.0,
                "failure": None, "updated_at": 0.0,
            }
            _PINNED[key] = entry
        entry["requests"] = int(entry.get("requests") or 0) + 1
        entry["mode"] = mode
        entry["provider"] = provider
        entry["model"] = model
        entry["api_mode"] = api_mode
        entry["updated_at"] = time.time()
        _PINNED.move_to_end(key)
        while len(_PINNED) > max(1, int(settings["max_turns"])):
            _PINNED.popitem(last=False)
    return entry


def _clear_session_state(session_id: Optional[str], *, clear_history: bool = False) -> None:
    """Drop one conversation's in-memory decisions and child registration."""
    if not session_id:
        return
    session = str(session_id)
    with _lock:
        # Prefer the stored conversation id: session ids may themselves contain
        # slashes, so splitting a composite decision key is not unambiguous.
        turn_keys = [
            key for key, entry in _SESSIONS.items()
            if str(entry.get("conversation_id") or _session_of(key)) == session
        ]
        route_keys = [
            key for key, entry in _PINNED.items()
            if str(entry.get("conversation_id") or _session_of(key)) == session
        ]
        route_keys.extend(
            _pin_key(session, _route_identity(entry.get("provider"), entry.get("model"),
                                             entry.get("api_mode")))
            for key, entry in _SESSIONS.items()
            if key in turn_keys
        )
        # Every turn of that session, plus the bare key used when no turn id was
        # available: ending a session must not leave decisions behind.
        for key in turn_keys:
            _SESSIONS.pop(key, None)
            _IN_FLIGHT.discard(key)
        for key in route_keys:
            _PINNED.pop(key, None)
            _ROUTE_IN_FLIGHT.discard(key)
        for key in list(_CLAUDE_HISTORY):
            if _CLAUDE_HISTORY[key].get("session_id") == session:
                _CLAUDE_HISTORY.pop(key, None)
        _IN_FLIGHT.discard(session)
        _CHILD_GOALS.pop(session, None)
    _publish_desktop_decision(session, clear=True)
    if clear_history:
        _history_store.clear_conversation((session,))


def on_session_end(session_id: Optional[str] = None, **kwargs: Any) -> None:
    """Handle Hermes' per-turn completion hook without erasing conversation status.

    Hermes supplies ``turn_id`` after each completed ``run_conversation`` call. Keep
    the bounded decision entries so status surfaces can still report this
    conversation's latest effort. Older/session-wide calls without a turn id retain
    their cleanup behavior; actual session boundaries use the hooks below.
    """
    turn_id = kwargs.get("turn_id")
    if turn_id is not None and str(turn_id).strip():
        return
    _clear_session_state(session_id)


def on_session_finalize(session_id: Optional[str] = None, **kwargs: Any) -> None:
    """Clear state when Hermes closes a conversation at a real session boundary."""
    _clear_session_state(session_id)


def on_session_reset(session_id: Optional[str] = None,
                     old_session_id: Optional[str] = None,
                     **kwargs: Any) -> None:
    """Clear the conversation replaced by a Hermes reset/session rotation."""
    _clear_session_state(old_session_id or session_id, clear_history=True)


# ── subagent registry ────────────────────────────────────────────────────────

def on_subagent_start(parent_session_id: Optional[str] = None,
                      child_session_id: Optional[str] = None,
                      child_goal: Optional[str] = None,
                      **kwargs: Any) -> None:
    """Verified hook: remember which session ids are subagents, and their goal.

    Both kwargs come straight from ``tools/delegate_tool.py``; the goal is stored
    verbatim because it IS the parent's own terse description of the work — the
    text a classifier should read. A child with no goal is not registered: the
    fallback (its own prompt) is no worse than not routing it at all.
    """
    if not child_session_id:
        return
    goal = str(child_goal or "").strip()
    if not goal:
        return
    with _lock:
        _CHILD_GOALS[str(child_session_id)] = goal
        _CHILD_GOALS.move_to_end(str(child_session_id))
        cap = _child_cap()
        while len(_CHILD_GOALS) > cap:
            _CHILD_GOALS.popitem(last=False)


def on_subagent_stop(parent_session_id: Optional[str] = None,
                     child_session_id: Optional[str] = None,
                     **kwargs: Any) -> None:
    """Verified hook: drop the child's registration once it is done."""
    if not child_session_id:
        return
    with _lock:
        _CHILD_GOALS.pop(str(child_session_id), None)


def _child_cap() -> int:
    """Bounded registry size. Reuses the decision bound; never a second setting."""
    try:
        return max(1, int(_read_setting("max_turns", DEFAULTS["max_turns"])))
    except Exception:
        return int(DEFAULTS["max_turns"])


def _child_goal(session_id: str) -> Optional[str]:
    """The parent's goal for *session_id*, or ``None`` when it is not a child."""
    with _lock:
        return _CHILD_GOALS.get(str(session_id))


# ── settings ────────────────────────────────────────────────────────────────

def _live_config() -> Dict[str, Any]:
    """The operator's profile config, or ``{}`` when it cannot be read.

    ``_config_reader`` is the seam a test run injects so it never reads the live
    profile: a live profile must not affect the default-mode assertions once the
    Hermes core is importable. In
    production the reader is ``None`` and the real config is read per call.
    """
    reader = _config_reader
    if reader is not None:
        try:
            return reader() or {}
        except Exception:
            return {}
    try:
        from hermes_cli.config import load_config_readonly
        return load_config_readonly() or {}
    except Exception:
        return {}


def _read_setting(key: str, default: Any = None) -> Any:
    """``plugins.entries.hermes-adaptive-effort.settings.<key>``, via ctx when present."""
    provider = _settings_provider
    if provider is not None:
        try:
            value = provider(key, default)
            return default if value in (None, "") else value
        except Exception:
            return default
    entry = ((_live_config().get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
    value = (entry.get("settings") or {}).get(key)
    return default if value in (None, "") else value


def _read_setting_if_present(key: str) -> Tuple[bool, Any]:
    """Read a setting while distinguishing an explicit empty value from absence."""
    provider = _settings_provider
    if provider is not None:
        missing = object()
        try:
            value = provider(key, missing)
        except Exception:
            return False, None
        return (False, None) if value is missing else (True, value)
    entry = ((_live_config().get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
    settings = entry.get("settings") or {}
    if not isinstance(settings, dict) or key not in settings:
        return False, None
    return True, settings[key]


def _canonical_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in VALID_MODES else "off"


def _settings() -> Dict[str, Any]:
    mode = _canonical_mode(_read_setting("mode", DEFAULTS["mode"]))
    mode_source = "config"
    override = mode_override()
    if override is not None:
        # A runtime choice outranks the file: /hae just told the operator it
        # applies to future requests, so it must.
        mode, mode_source = override, "override"
    # Independent gate: children are routed only when BOTH the session mode and
    # this one allow it, so opting into session routing never silently starts
    # rewriting subagent effort.
    subagent_mode = _canonical_mode(
        _read_setting("subagent_mode", DEFAULTS["subagent_mode"]))

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

    def _bool(key: str, fallback: bool) -> bool:
        value = _read_setting(key, fallback)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "on", "1"}:
                return True
            if normalized in {"false", "no", "off", "0", ""}:
                return False
        return fallback

    effort_models = _read_setting("effort_models", DEFAULTS["effort_models"])

    settings = {
        "mode": mode,
        "mode_source": mode_source,
        "subagent_mode": subagent_mode,
        "timeout_s": _num("timeout_s", DEFAULTS["timeout_s"]),
        "max_turns": _int("max_turns", DEFAULTS["max_turns"]),
        "prompt_chars": _int("prompt_chars", DEFAULTS["prompt_chars"]),
        "effort_models": _normalize_forced_models(effort_models),
        "endpoint": str(_read_setting("endpoint", DEFAULTS["endpoint"])),
        # The URL the client will actually POST to: the setting above may name the
        # API base instead of the scoring route, and a mismatch is invisible until
        # every classification fails open. Reported so `status` shows the truth.
        "scorer_provider": str(_read_setting(
            "scorer_provider", DEFAULTS["scorer_provider"]) or _scorers.JEV).strip().lower(),
        "jev_model": str(_read_setting(
            "jev_model", DEFAULTS["jev_model"]) or DEFAULTS["jev_model"]).strip()
        or DEFAULTS["jev_model"],
        "scorer_model": str(_read_setting(
            "scorer_model", DEFAULTS["scorer_model"]) or "").strip(),
        "custom_endpoint": str(_read_setting(
            "custom_endpoint", DEFAULTS["custom_endpoint"]) or "").strip(),
        "custom_api_format": str(_read_setting(
            "custom_api_format", DEFAULTS["custom_api_format"]) or "systemone").strip().lower(),
        "custom_auth": str(_read_setting(
            "custom_auth", DEFAULTS["custom_auth"]) or "none").strip().lower(),
        "cloudflare_account_id": str(_read_setting(
            "cloudflare_account_id", DEFAULTS["cloudflare_account_id"]) or "").strip(),
        "cloudflare_model": _scorers.cloudflare_client.normalize_model_selector(
            _read_setting("cloudflare_model", DEFAULTS["cloudflare_model"])),
        "classification_instructions": _rubric.normalize_classification_instructions(
            _read_setting("classification_instructions", DEFAULTS["classification_instructions"])),
        "use_target_model_context": _bool(
            "use_target_model_context", DEFAULTS["use_target_model_context"]),
        "show_tui_status": _bool("show_tui_status", DEFAULTS["show_tui_status"]),
        "show_desktop_popup": _bool(
            "show_desktop_popup", DEFAULTS["show_desktop_popup"]),
    }
    settings["scorer_model_effective"] = _scorers.model_for(
        settings["scorer_provider"], settings["scorer_model"], settings["cloudflare_model"],
        settings["jev_model"])
    (settings["scorer_endpoint"], settings["scorer_endpoint_effective"]) = \
        _scorers.endpoint_for(settings["scorer_provider"], settings["endpoint"],
                              settings["cloudflare_account_id"], settings["cloudflare_model"],
                              settings["custom_endpoint"])
    settings["cloudflare_account_ready"] = _scorers.cloudflare_client.valid_account_id(
        settings["cloudflare_account_id"])
    # This compatibility key has always meant "where the active scorer posts";
    # preserve that meaning even when OpenRouter is selected.
    settings["endpoint_effective"] = settings["scorer_endpoint_effective"]
    return settings


def _normalize_forced_models(raw: Any) -> Tuple[str, ...]:
    """Parse exact operator-declared model IDs; malformed setting types are ignored."""
    if not isinstance(raw, str):
        return ()
    models = set()
    for line in raw.replace("\r", "\n").split("\n"):
        for item in line.split(","):
            model = item.strip().lower().rsplit("/", 1)[-1]
            if model:
                models.add(model)
    return tuple(sorted(models))


def _classifies(client: Any) -> bool:
    """True when *client* exposes the classification surface we drive."""
    return (callable(getattr(client, "classify", None))
            or callable(getattr(client, "classify_detail", None))
            or callable(getattr(client, "classify_effort_detail", None)))


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
            logger.debug("hermes-adaptive-effort: classifier factory raised; failing open",
                         exc_info=True)
            return None
    except Exception:
        logger.debug("hermes-adaptive-effort: classifier factory raised; failing open", exc_info=True)
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


def _classify(prompt: str, settings: Dict[str, Any],
              choices: Optional[Tuple[str, ...]] = None) -> "Tuple[Any, Optional[str]]":
    """Return one legacy score or one strict named route choice plus failure.

    ``failure`` is ``None`` when a score came back, otherwise a reason code from
    the documented table in :mod:`jev_client` plus ``classifier_error`` (the
    injected client raised, was unavailable, or answered nothing at all). Every
    path fails open — this function never raises.
    """
    factory = _classifier_factory
    if factory is None:
        client, build_failure = _scorers.build_client(settings)
        if client is None:
            return None, build_failure or "classifier_error"
    else:
        client = _build_client(factory, settings)
        if client is None:
            return None, "classifier_error"
    detail: Any = getattr(client, "classify_effort_detail", None) if choices else None
    try:
        if choices:
            if not callable(detail):
                return None, "unsupported_provider"
            result, failure = detail(prompt, choices)
            if result is None:
                return None, failure or "classifier_error"
            if not isinstance(result, str) or result not in choices:
                return None, "malformed_response"
            return result, None
        detail = getattr(client, "classify_detail", None)
        if callable(detail):
            score, failure = detail(prompt)
        else:
            score, failure = client.classify(prompt), None
    except Exception:
        logger.debug("hermes-adaptive-effort: classifier raised; failing open", exc_info=True)
        return None, "classifier_error"
    if score is None:
        return None, failure or "classifier_error"
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None, "malformed_response"
    return score, None


def run_probe(prompt: str,
              settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Exactly one bounded classification for ``/hae probe``.

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

def _latest_user_text(messages: Any) -> Optional[str]:
    """Latest user message: history and trailing tool results are not the new task."""
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
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


def _latest_responses_text(input_items: Any) -> Optional[str]:
    """Latest user text of a *preflighted* ``codex_responses`` payload.

    On that route the middleware sees the payload AFTER
    ``agent._get_transport().preflight_kwargs()`` has replaced ``messages`` with
    ``input`` (``codex_responses_adapter._preflight_codex_api_kwargs``), so
    ``_latest_user_text(request["messages"])`` is always ``None`` and every normal
    Codex session was a silent no-op — only subagents, classified from the goal
    their parent wrote, ever worked.
    """
    if not isinstance(input_items, list):
        return None
    for item in reversed(input_items):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content
                     if isinstance(part, dict) and isinstance(part.get("text"), str)]
            text = "".join(parts)
            if text.strip():
                return text
    return None


def _latest_user_prompt(request: Dict[str, Any]) -> Optional[str]:
    """Classification target of a request in any of the shapes we can see.

    Chat Completions carries ``messages``; a preflighted Codex payload carries
    ``input`` instead. Read both so the decision does not depend on the api_mode.
    """
    prompt = _latest_user_text(request.get("messages"))
    if prompt:
        return prompt
    return _latest_responses_text(request.get("input"))


def _effort_slot(request: Dict[str, Any], provider: Any = None, model: Any = None,
                 api_mode: Any = None, base_url: Any = None
                 ) -> Optional[Tuple[Dict[str, Any], str, str]]:
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
    # codex_responses (and the Responses family generally) carries effort in a
    # TOP-LEVEL ``reasoning`` object — verified live against ResponsesApiTransport:
    # build_kwargs() returns reasoning={"effort":…,"summary":…} with extra_body
    # absent. Without this branch every subagent on our delegation route would be
    # a silent no-op, because the parent session never uses it.
    reasoning = request.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("enabled") is False:
            return None
        value = reasoning.get("effort")
        if (isinstance(value, str) and value.strip()
                and value.strip().lower() != "none"):
            return reasoning, "effort", value.strip().lower()
    if (str(provider or "").strip().lower() == "anthropic"
            and _effort.route_choice_levels(provider, model, api_mode, base_url)):
        output_config = request.get("output_config")
        if isinstance(output_config, dict) and "effort" in output_config:
            value = output_config.get("effort")
            if isinstance(value, str) and value.strip() and value.strip().lower() != "none":
                return output_config, "effort", value.strip().lower()
    return None


def _reasoning_is_explicitly_disabled(request: Dict[str, Any]) -> bool:
    """Do not rewrite or insert effort when a host control disables or malforms thinking."""
    reasoning = request.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("enabled") is False:
            return True
        if "enabled" in reasoning and not isinstance(reasoning.get("enabled"), bool):
            return True
    thinking = request.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        return True
    extra = request.get("extra_body")
    if isinstance(extra, dict):
        reasoning = extra.get("reasoning")
        if isinstance(reasoning, dict):
            if reasoning.get("enabled") is False:
                return True
            if "enabled" in reasoning and not isinstance(reasoning.get("enabled"), bool):
                return True
        thinking = extra.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            return True
    return False


def _apply(request: Dict[str, Any], slot: Tuple[Dict[str, Any], str, str],
           target: str) -> Dict[str, Any]:
    """Full replacement payload with exactly one value changed.

    ``slot`` is ``(container, key, old)`` where ``container`` is the dict that
    actually holds the value. Writing back into that same container — rather than
    rebuilding a fixed path — is what keeps a read/write pair honest: any shape
    ``_effort_slot`` can read is a shape ``_apply`` can write, so a discovered
    slot can never degrade into a silent no-op.
    """
    container, key, old = slot
    if target == old:
        return request
    new = dict(request)
    new_container = dict(container)
    new_container[key] = target
    if container is request:
        new[key] = target
        return new
    if container is request.get("output_config"):
        new["output_config"] = new_container
        return new
    if container is new.get("extra_body", {}).get("reasoning"):
        extra_body = dict(request.get("extra_body") or {})
        extra_body["reasoning"] = new_container
        new["extra_body"] = extra_body
        return new
    # Nested top-level container (codex_responses ``reasoning``): replace that
    # key with the mutated copy, leaving every sibling untouched.
    for owner_key, owner in request.items():
        if owner is container:
            new[owner_key] = new_container
            return new
    return request


# ── route-aware reuse of a stored decision ──────────────────────────────────

_MUSE_ZEN_PROVIDERS = frozenset({"opencode", "opencode-zen", "opencode_zen", "zen"})
_MUSE_GO_PROVIDERS = frozenset({"opencode-go", "opencode_go", "go", "opencode-go-sub"})


def _injection_path(request: Dict[str, Any], provider: Any, model: Any,
                    api_mode: Any, forced_models: Tuple[str, ...] = (),
                    base_url: Any = None) -> Optional[str]:
    """Verified provider/model/API combinations; never use the generic fallback.

    The registry binds exact provider-family routes to a writable field shape.
    Existing malformed/disabled controls are never an invitation to inject.
    """
    provider_name = str(provider or "").strip().lower()
    bare_model = str(model or "").strip().lower().rsplit("/", 1)[-1]
    api = str(api_mode or "").strip().lower()
    claude_choices = (_effort.route_choice_levels(provider_name, model, api, base_url)
                      if provider_name == "anthropic" else ())
    if claude_choices:
        output_config = request.get("output_config")
        if output_config is not None and not isinstance(output_config, dict):
            return None
        if isinstance(output_config, dict) and "effort" in output_config:
            return None
        if "reasoning_effort" in request or "reasoning" in request:
            return None
        if isinstance(request.get("thinking"), dict) and \
                request["thinking"].get("type") == "between_tools":
            return None
        return "output_config.effort"
    if provider_name in _MUSE_ZEN_PROVIDERS and api == "codex_responses":
        if bare_model in {"muse-spark-1.3", "muse-spark-1.2",
                          "muse-spark-1.3-contributor-free"}:
            route = ("reasoning", _effort.MUSE_INJECTION_EFFORTS.get(bare_model))
        else:
            route = None
    elif provider_name in _MUSE_GO_PROVIDERS:
        route = _effort.OPEN_CODE_GO_INJECTION_ROUTES.get(api, {}).get(bare_model)
    else:
        route = None
    if route is None and bare_model in forced_models:
        if api == "codex_responses":
            route = ("reasoning", ())
        elif api == "chat_completions":
            route = ("reasoning_effort", ())
        else:
            return None
    if route is None:
        return None
    path, _vocabulary = route
    if "reasoning_effort" in request or "thinking" in request:
        return None
    reasoning = request.get("reasoning", {})
    if not isinstance(reasoning, dict) or "effort" in reasoning or reasoning.get("enabled") is False:
        return None
    extra = request.get("extra_body", {})
    if not isinstance(extra, dict):
        return None
    if path == "paired_effort":
        thinking = extra.get("thinking")
        if not isinstance(thinking, dict) or thinking.get("type") != "enabled":
            return None
        if any(key in extra for key in ("reasoning", "reasoning_effort")):
            return None
        return "reasoning_effort"
    if any(key in extra for key in ("reasoning", "reasoning_effort", "thinking")):
        return None
    return path


def _inject(request: Dict[str, Any], path: str, target: str) -> Dict[str, Any]:
    """Copy only the owners of the new field, preserving caller-owned siblings."""
    new = dict(request)
    if path == "output_config.effort":
        new["output_config"] = dict(request.get("output_config") or {}, effort=target)
    elif path == "reasoning":
        new["reasoning"] = dict(request.get("reasoning", {}), effort=target)
    else:
        new["reasoning_effort"] = target
    return new

def _target_for_route(entry: Dict[str, Any], provider: Any, model: Any,
                      api_mode: Any = None, base_url: Any = None) -> Optional[str]:
    """The wire target *entry* gets on the CURRENT route, or ``None``.

    A stored target is only legal for the route that produced it. A provider
    fallback inside one turn — or a retained session/route decision followed by
    a route change — keeps the same decision key while the route
    changes underneath it, and applying the recorded level verbatim is exactly
    how a narrow route receives a value its vendor rejects (HTTP 400).

    The label is the route-independent part (it describes the PROMPT), so on a
    route change it is re-clamped onto the new route's wire vocabulary. The
    re-clamped value is written back, so the record always shows what was
    actually applied. ``None`` means the new route cannot express the label at
    all: the caller reports ``unsupported`` and rewrites nothing.
    """
    label = entry.get("label")
    if not isinstance(label, str) or not label:
        return None
    if (entry.get("provider"), entry.get("model"), entry.get("api_mode")) == (
            provider, model, api_mode):
        target = entry.get("target")
        return target if isinstance(target, str) and target else None
    choices = _effort.route_choice_levels(provider, model, api_mode, base_url)
    if entry.get("decision_type") == "native_choice" and choices:
        target = _effort.map_named_choice(label, choices)
        entry["choices"] = list(choices)
    else:
        target = _effort.map_effort(label, provider, model)
    entry["target"] = target
    entry["provider"] = provider
    entry["model"] = model
    entry["api_mode"] = api_mode
    return target


def _anthropic_beta_header(request: Any) -> Optional[Tuple[str, str]]:
    """Find an existing mutable beta header without discarding its other values."""
    if not isinstance(request, dict):
        return None
    headers = request.get("extra_headers")
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "anthropic-beta" \
                and isinstance(value, str):
            return key, value
    return None


def _anthropic_beta_ready(request: Any) -> bool:
    """The request must expose the effective header so the plugin can add the beta."""
    return _anthropic_beta_header(request) is not None


def _with_anthropic_effort_beta(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Add the per-message beta to an existing Anthropic header, preserving siblings."""
    header = _anthropic_beta_header(request)
    if header is None:
        return None
    header_name, value = header
    required = "mid-conversation-output-config-2026-07-01"
    if required in {item.strip() for item in value.split(",") if item.strip()}:
        return request
    separator = "" if not value or value.rstrip().endswith(",") else ","
    headers = dict(request["extra_headers"])
    headers[header_name] = value + separator + required
    updated = dict(request)
    updated["extra_headers"] = headers
    return updated


def _dynamic_effort_route(provider: Any, model: Any, api_mode: Any,
                          request: Any = None, base_url: Any = None) -> bool:
    """True only for exact model/API routes with explicit effort and cache evidence."""
    route = _route_identity(provider, model, api_mode)
    provider_name, model_id, api = route
    bare_model = model_id.lower().rsplit("/", 1)[-1]
    if provider_name == "anthropic" and api == "anthropic_messages":
        return (
            bare_model in _effort.ANTHROPIC_PER_MESSAGE_MODELS
            and bool(_effort.route_choice_levels(provider_name, model_id, api, base_url))
            and _anthropic_beta_ready(request)
            and isinstance(request, dict)
            and isinstance(request.get("messages"), list)
            and not (isinstance(request.get("thinking"), dict)
                     and request["thinking"].get("type") == "between_tools")
        )
    if not _cache_safety.effort_is_cache_safe(provider_name, model_id, api):
        return False
    if provider_name == "openai-codex" and api == "codex_responses":
        return bare_model == "gpt-6.1-sol"
    if provider_name == "opencode-zen" and api == "codex_responses":
        return bare_model in {
            "muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.3-contributor-free",
        }
    if provider_name == "opencode-go":
        return bare_model in _effort.OPEN_CODE_GO_INJECTION_ROUTES.get(api, {})
    return False


def _claude_anchor_content(content: Any) -> Any:
    """Drop request-local prompt-cache decoration so a turn hashes the same every request.

    Hermes marks only the latest messages, turning a string into text parts (possibly split
    at a registered scaffold) with ``cache_control``; the marker moves as the chat grows."""
    if not isinstance(content, list):
        return content
    parts = [
        {k: v for k, v in block.items() if k != "cache_control"}
        if isinstance(block, dict) else block
        for block in content
    ]
    if parts and all(isinstance(block, dict) and set(block) == {"type", "text"}
                     and block["type"] == "text" and isinstance(block["text"], str)
                     for block in parts):
        return "".join(block["text"] for block in parts)
    return parts


def _claude_user_anchors(messages: Any) -> Tuple[Dict[Tuple[int, str], int],
                                                   Optional[Tuple[int, str]]]:
    """Hash user-message positions without retaining their content."""
    if not isinstance(messages, list):
        return {}, None
    anchors: Dict[Tuple[int, str], int] = {}
    latest = None
    ordinal = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content):
            continue
        ordinal += 1
        try:
            serialized = json.dumps(_claude_anchor_content(content), ensure_ascii=True,
                                    sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return {}, None
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        latest = (ordinal, digest)
        anchors[latest] = index
    return anchors, latest


def _claude_initial_effort(request: Dict[str, Any]) -> Optional[str]:
    config = request.get("output_config")
    value = config.get("effort") if isinstance(config, dict) else None
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _claude_history_snapshot(route_key: str, request: Dict[str, Any], max_turns: int
                             ) -> Tuple[Optional[Dict[str, Any]], Optional[str],
                                        Optional[Tuple[int, str]], bool]:
    """Validate stored marker anchors and the initial setting before reuse."""
    anchors, latest = _claude_user_anchors(request.get("messages"))
    if latest is None:
        return None, "cache_continuity_invalid", None, False
    with _lock:
        current = _CLAUDE_HISTORY.get(route_key)
        state = dict(current) if isinstance(current, dict) else None
        if state is not None:
            state["markers"] = [dict(marker) for marker in state.get("markers", [])]
    if state is None:
        with _lock:
            registry_full = len(_CLAUDE_HISTORY) >= max(1, int(max_turns))
        if registry_full:
            return {"session_id": _session_of(route_key), "invalid": True,
                    "markers": []}, "cache_continuity_invalid", latest, False
        return {"session_id": _session_of(route_key),
                "initial": _claude_initial_effort(request), "markers": []}, None, latest, False
    if state.get("invalid"):
        return state, "cache_continuity_invalid", latest, False
    if state.get("initial") != _claude_initial_effort(request):
        return state, "cache_continuity_invalid", latest, False
    for marker in state.get("markers", []):
        anchor = (marker.get("ordinal"), marker.get("anchor"))
        if anchor not in anchors:
            return state, "cache_continuity_invalid", latest, False
    current_has_marker = any(
        marker.get("ordinal") == latest[0] and marker.get("anchor") == latest[1]
        for marker in state.get("markers", [])
    )
    full = len(state.get("markers", [])) >= max(1, int(max_turns)) and not current_has_marker
    return state, None, latest, full


def _is_effort_marker(message: Any) -> Optional[str]:
    if not isinstance(message, dict) or message.get("role") != "system" \
            or message.get("content") != []:
        return None
    config = message.get("output_config")
    if not isinstance(config, dict) or set(config) != {"effort"}:
        return None
    effort = config.get("effort")
    return effort if isinstance(effort, str) else None


def _claude_apply_choice(route_key: str, request: Dict[str, Any], target: str,
                         max_turns: int) -> Tuple[Optional[Dict[str, Any]], str, bool,
                                                  Optional[str]]:
    """Replay stored per-turn markers and append one validated choice, if needed."""
    state, failure, latest, full = _claude_history_snapshot(route_key, request, max_turns)
    if failure or state is None or latest is None:
        return None, "absent", False, failure or "cache_continuity_invalid"
    markers = [dict(marker) for marker in state.get("markers", [])]
    effective_before = (markers[-1].get("effort") if markers else state.get("initial")) or "absent"
    changed_effort = target != effective_before
    if changed_effort:
        if full:
            target = effective_before if effective_before != "absent" else target
            changed_effort = False
        else:
            markers.append({"ordinal": latest[0], "anchor": latest[1], "effort": target})

    anchors, _ = _claude_user_anchors(request.get("messages"))
    anchor_by_index = {index: digest for (ordinal, digest), index in anchors.items()}
    markers_by_anchor: Dict[Tuple[int, str], list] = {}
    for marker in markers:
        key = (marker["ordinal"], marker["anchor"])
        markers_by_anchor.setdefault(key, []).append(marker["effort"])
    output = []
    ordinal = 0
    messages = request.get("messages")
    if not isinstance(messages, list):
        return None, effective_before, False, "cache_continuity_invalid"
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list) and any(
                    isinstance(block, dict) and block.get("type") == "tool_result"
                    for block in content):
                output.append(message)
                continue
            ordinal += 1
            key = (ordinal, anchor_by_index.get(index, ""))
            expected = markers_by_anchor.get(key, [])
            if expected:
                observed = []
                cursor = len(output)
                while cursor > 0:
                    effort = _is_effort_marker(output[cursor - 1])
                    if effort is None:
                        break
                    observed.insert(0, effort)
                    cursor -= 1
                if observed and observed != expected:
                    return None, effective_before, changed_effort, "cache_continuity_invalid"
                if observed != expected:
                    output.extend({"role": "system", "content": [],
                                   "output_config": {"effort": effort}}
                                  for effort in expected)
        output.append(message)
    new_request = request
    if output != messages:
        new_request = dict(request)
        new_request["messages"] = output
    if markers:
        new_request = _with_anthropic_effort_beta(new_request)
        if new_request is None:
            return None, effective_before, changed_effort, "cache_continuity_invalid"
    next_state = {
        "session_id": state.get("session_id"),
        "initial": state.get("initial"),
        "markers": markers,
    }
    with _lock:
        if route_key not in _CLAUDE_HISTORY and \
                len(_CLAUDE_HISTORY) >= max(1, int(max_turns)):
            return None, effective_before, changed_effort, "cache_continuity_invalid"
        _CLAUDE_HISTORY[route_key] = next_state
        _CLAUDE_HISTORY.move_to_end(route_key)
    return new_request, effective_before, changed_effort, None


def _invalidate_claude_history(route_key: str) -> None:
    with _lock:
        existing = _CLAUDE_HISTORY.get(route_key)
        if existing:
            _CLAUDE_HISTORY[route_key] = {
                "session_id": existing.get("session_id"), "invalid": True, "markers": []}
            _CLAUDE_HISTORY.move_to_end(route_key)


def _copy_decision(source: Dict[str, Any], target: Dict[str, Any]) -> None:
    """Copy only prompt-free decision fields between the turn and route ledgers."""
    for field in ("state", "label", "target", "score", "failure", "elapsed_ms",
                  "probes", "provider", "model", "api_mode", "scorer_provider",
                  "scorer_model", "updated_at", "decision_type", "choices",
                  "cache_behavior", "_applied_from"):
        target[field] = source.get(field)


# ── the middleware itself ───────────────────────────────────────────────────

def on_llm_request(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Fail-open: returns ``None`` for every path that must not change a request."""
    try:
        return _handle(kwargs)
    except Exception:
        session_id = str(kwargs.get("session_id") or "")
        if (session_id and str(kwargs.get("provider") or "").lower() == "anthropic"
                and str(kwargs.get("api_mode") or "").lower() == "anthropic_messages"):
            _invalidate_claude_history(_pin_key(
                session_id, _route_identity(kwargs.get("provider"), kwargs.get("model"),
                                            kwargs.get("api_mode"))))
        logger.debug("hermes-adaptive-effort: middleware error; failing open", exc_info=True)
        return None


def _handle(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request = kwargs.get("request")
    if not isinstance(request, dict):
        return None

    settings = _settings()
    _sync_cli_status(settings)
    mode = settings["mode"]
    if mode == "off":
        session_id = str(kwargs.get("session_id") or "")
        if session_id:
            key = _decision_key(session_id, kwargs.get("turn_id"))
            with _lock:
                prior = _SESSIONS.get(key)
                preserve_decision = bool(prior and prior.get("label"))
            entry = _touch(
                key, settings, mode, kwargs.get("provider"), kwargs.get("model"),
                kwargs.get("api_mode"), conversation_id=session_id,
            )
            # The Desktop popup can name the active route while scoring is off.
            # Preserve a prior same-turn decision as memo data, but never classify
            # or change the request while this mode is active.
            with _lock:
                if not preserve_decision:
                    entry.update(provider=kwargs.get("provider"), model=kwargs.get("model"),
                                 api_mode=kwargs.get("api_mode"))
                    entry.update(state="off", score=None, label=None, target=None, failure=None)
            off_entry = dict(entry)
            off_entry.update(state="off", score=None, label=None, target=None, failure=None)
            _publish_desktop_decision(
                session_id, kwargs.get("provider"), kwargs.get("model"),
                kwargs.get("api_mode"), off_entry)
        return None  # no classification or request change

    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        logger.debug("hermes-adaptive-effort: no session id; failing open")
        return None

    provider = kwargs.get("provider")
    model = kwargs.get("model")
    api_mode = kwargs.get("api_mode")

    # A subagent is classified from the goal its PARENT wrote, not from its own
    # first prompt. subagent_mode is a second, independent gate: the session mode
    # alone never starts rewriting children's effort.
    child_goal = _child_goal(session_id)
    if child_goal is not None and settings["subagent_mode"] == "off":
        return None
    if child_goal is not None:
        mode = settings["subagent_mode"]

    base_url = kwargs.get("base_url")
    slot = _effort_slot(request, provider, model, api_mode, base_url)
    injection_path = None
    if slot is None and mode in ("auto", "once", "always"):
        injection_path = _injection_path(
            request, provider, model, api_mode, settings["effort_models"], base_url)
    thinking = request.get("thinking")
    if (str(provider or "").strip().lower() == "anthropic"
            and str(api_mode or "").strip().lower() == "anthropic_messages"
            and _effort.route_choice_levels(provider, model, api_mode, base_url)
            and isinstance(thinking, dict) and thinking.get("type") == "between_tools"):
        slot = None
        injection_path = None
    if _reasoning_is_explicitly_disabled(request):
        key = _decision_key(session_id, kwargs.get("turn_id"))
        entry = _touch(key, settings, mode, provider, model, api_mode,
                       conversation_id=session_id)
        if entry.get("state") == "new":
            entry.update(state="unsupported", failure="reasoning_disabled")
        _publish_desktop_decision(session_id, provider, model, api_mode, entry)
        return None
    if slot is None and injection_path is None:
        # Report the route to status without creating a persistent decision or
        # spending a scorer call; a later eligible request can still be handled.
        key = _decision_key(session_id, kwargs.get("turn_id"))
        entry = _touch(key, settings, mode, provider, model, api_mode,
                       conversation_id=session_id)
        if entry.get("state") == "new":
            entry.update(state="unsupported", failure="effort_control_unsupported")
        _publish_desktop_decision(session_id, provider, model, api_mode, entry)
        logger.debug("hermes-adaptive-effort: no writable effort field; no change")
        return None

    turn_id = kwargs.get("turn_id")
    turn_key = _decision_key(session_id, turn_id)
    route = _route_identity(provider, model, api_mode)
    route_choices = _effort.route_choice_levels(provider, model, api_mode, base_url)
    dynamic_route = _dynamic_effort_route(
        provider, model, api_mode, request=request, base_url=base_url)
    persistent_scope = (
        mode == "once"
        or (mode == "auto" and not dynamic_route)
        or not str(turn_id or "").strip()
    )
    route_key = _pin_key(session_id, route)
    claude_per_message = (
        not persistent_scope and mode in ("auto", "always")
        and str(provider or "").strip().lower() == "anthropic"
        and str(api_mode or "").strip().lower() == "anthropic_messages"
        and dynamic_route
    )
    claude_history = None
    claude_latest_anchor = None
    claude_at_capacity = False
    if claude_per_message:
        claude_history, history_failure, claude_latest_anchor, claude_at_capacity = \
            _claude_history_snapshot(route_key, request, settings["max_turns"])
        if history_failure:
            _invalidate_claude_history(route_key)
            key = _decision_key(session_id, turn_id)
            entry = _touch(key, settings, mode, provider, model, api_mode,
                           conversation_id=session_id)
            entry.update(state="unsupported", failure=history_failure, score=None,
                         cache_behavior="invalidated")
            _publish_desktop_decision(session_id, provider, model, api_mode, entry)
            return None
    seed_pin = False

    # Keep one per-turn record for tool-loop reuse, even when this route is
    # governed by a session pin. It also prevents a route fallback in the same
    # turn from spending a second scorer call.
    turn_entry = _touch(
        turn_key, settings, mode, provider, model, api_mode,
        conversation_id=session_id,
    )
    if "probe_owner" not in turn_entry:
        turn_entry["probe_owner"] = "session_route" if persistent_scope else "turn"

    with _lock:
        pinned_entry = _PINNED.get(route_key)
    if turn_entry.get("state") in ("decided", "unsupported", "failed") \
            and turn_entry.get("label"):
        # A route changed within an already-classified turn. Its prompt decision
        # wins for this tool loop; re-clamp it on the current model. Seed a pin
        # only when this route has not already retained its own session decision.
        entry = turn_entry
        seed_pin = persistent_scope and pinned_entry is None
    elif persistent_scope and pinned_entry is not None:
        entry = _touch_pin(route_key, settings, mode, provider, model, api_mode, session_id)
        # A pre-existing route pin wins when this is the first eligible request
        # of the turn. Remember it on the turn record to prevent another scorer.
        if turn_entry.get("state") in ("new", "probing"):
            _copy_decision(entry, turn_entry)
            turn_entry["probe_owner"] = "session_route"
    else:
        entry = turn_entry

    if entry.get("state") == "failed":
        return None
    if entry.get("state") == "probing":
        return None

    if claude_per_message and claude_at_capacity \
            and (entry.get("state") not in ("decided", "unsupported")
                 or not entry.get("label")):
        markers = claude_history.get("markers", []) if claude_history else []
        retained = (markers[-1].get("effort") if markers else None) \
            or (claude_history.get("initial") if claude_history else None)
        if not retained:
            _invalidate_claude_history(route_key)
            entry.update(state="unsupported", failure="cache_continuity_invalid",
                         cache_behavior="invalidated")
            _publish_desktop_decision(session_id, provider, model, api_mode, entry)
            return None
        entry.update(
            state="decided", decision_type="fixed", choices=list(route_choices),
            score=None, label=retained, target=retained, failure=None,
            cache_behavior="per_message", updated_at=time.time(),
        )

    if entry.get("state") not in ("decided", "unsupported") or not entry.get("label"):
        # A subagent classifies the terse goal its parent wrote; a normal session
        # classifies its current user message, not the oldest one in its history.
        prompt = child_goal if child_goal is not None else _latest_user_prompt(request)
        if not prompt:
            return None
        # One scorer call per user turn even when a provider changes route during
        # a tool loop. Persistent decisions are copied into the route ledger below.
        claim_key = turn_key
        fixed_choice = len(route_choices) == 1
        if fixed_choice:
            label = route_choices[0]
            entry.update(
                state="decided", decision_type="fixed", choices=list(route_choices),
                score=None, label=label, target=label, failure=None, elapsed_ms=0.0,
                cache_behavior=("per_message" if claude_per_message else
                                "top_level_cache_may_reset"
                                if str(provider or "").strip().lower() == "anthropic"
                                and mode == "always" else
                                "route_pinned" if str(provider or "").strip().lower()
                                == "anthropic" and persistent_scope else None),
                updated_at=time.time(),
            )
            if persistent_scope:
                pinned = _touch_pin(route_key, settings, mode, provider, model, api_mode, session_id)
                _copy_decision(entry, pinned)
        elif not _claim_scoring(
                claim_key, route_key, persistent_scope, serialize_route=claude_per_message):
            # Another request of this same session is classifying right now:
            # never a second scorer call, and its entry stays untouched.
            logger.debug("hermes-adaptive-effort: probe already in flight; failing open")
            return None
        else:
            try:
                entry = turn_entry
                entry["state"] = "probing"
                _publish_desktop_decision(session_id, provider, model, api_mode, entry)
                started = time.monotonic()
                scoring_prompt = prompt
                scoring_settings = settings
                if settings["use_target_model_context"]:
                    try:
                        task_text = _jev_client.truncate_prompt(prompt, settings["prompt_chars"])
                        context = _model_profiles.target_context(
                            provider, model, api_mode, slot[2] if slot is not None else None)
                        scoring_prompt = _model_profiles.wrap_task(task_text, context)
                        scoring_settings = dict(settings)
                        # Task text has its own cap; context is a separate, opt-in addition.
                        scoring_settings["prompt_chars"] = len(scoring_prompt)
                    except Exception:
                        logger.debug(
                            "hermes-adaptive-effort: target context preparation failed; failing open",
                            exc_info=True,
                        )
                        scoring_prompt = ""
                        scoring_settings = settings
                        result, failure = None, "classifier_error"
                    else:
                        result, failure = _classify(
                            scoring_prompt, scoring_settings,
                            tuple(route_choices) if len(route_choices) > 1 else None)
                else:
                    result, failure = _classify(
                        scoring_prompt, scoring_settings,
                        tuple(route_choices) if len(route_choices) > 1 else None)
                entry["elapsed_ms"] = (time.monotonic() - started) * 1000.0
                entry["probes"] = int(entry.get("probes") or 0) + 1
                entry["updated_at"] = time.time()
                entry["cache_behavior"] = (
                    "per_message" if claude_per_message else
                    "top_level_cache_may_reset"
                    if str(provider or "").strip().lower() == "anthropic" and mode == "always" else
                    "route_pinned" if str(provider or "").strip().lower() == "anthropic"
                    and persistent_scope else None)
                if failure or result is None:
                    entry.update(state="failed", score=None, failure=failure or "classifier_error")
                    if claude_per_message:
                        _invalidate_claude_history(route_key)
                    if persistent_scope:
                        pinned = _touch_pin(
                            route_key, settings, mode, provider, model, api_mode, session_id)
                        _copy_decision(entry, pinned)
                    _publish_desktop_decision(session_id, provider, model, api_mode, entry)
                    return None

                if len(route_choices) > 1:
                    label = result
                    entry.update(decision_type="native_choice", choices=list(route_choices),
                                 score=None, label=label)
                    target = _effort.map_effort(label, provider, model, route_choices)
                else:
                    score = result
                    entry["score"] = score
                    label = _effort.score_to_label(score)
                    entry.update(decision_type="legacy_score", choices=[], label=label)
                    target = _effort.map_effort(label, provider, model) if label else None
                entry["target"] = target
                entry["provider"] = provider
                entry["model"] = model
                entry["api_mode"] = api_mode
                entry["state"] = "decided" if target is not None else "unsupported"
                entry["failure"] = None if target is not None else "effort_value_unsupported"
                if target is None and label is None:
                    entry["state"] = "failed"
                    entry["failure"] = "malformed_response"
                if persistent_scope:
                    pinned = _touch_pin(
                        route_key, settings, mode, provider, model, api_mode, session_id)
                    _copy_decision(entry, pinned)
            finally:
                _release_scoring(
                    claim_key, route_key, persistent_scope, serialize_route=claude_per_message)

    target = _target_for_route(entry, provider, model, api_mode, base_url)
    if target is None:
        entry["state"] = "unsupported"
        entry["failure"] = "effort_value_unsupported"
        if seed_pin:
            pinned = _touch_pin(route_key, settings, mode, provider, model, api_mode, session_id)
            _copy_decision(entry, pinned)
            # This route inherits the turn's score; no scorer call was made for it.
            pinned["probes"] = 0
        _publish_desktop_decision(session_id, provider, model, api_mode, entry)
        return None
    entry["state"] = "decided"
    entry["failure"] = None
    if seed_pin:
        pinned = _touch_pin(route_key, settings, mode, provider, model, api_mode, session_id)
        _copy_decision(entry, pinned)
        # This route inherits the turn's score; no scorer call was made for it.
        pinned["probes"] = 0
    if claude_per_message:
        new_request, before, changed_effort, failure = _claude_apply_choice(
            route_key, request, target, settings["max_turns"])
        if failure or new_request is None:
            _invalidate_claude_history(route_key)
            entry.update(state="unsupported", failure=failure or "cache_continuity_invalid",
                         cache_behavior="invalidated")
            _publish_desktop_decision(session_id, provider, model, api_mode, entry)
            return None
        event = None
        if changed_effort:
            event = _record_effort_change(
                turn_key, before, target, session_id, provider, model, api_mode,
                details=entry)
            if event is not None:
                entry["_applied_from"] = before
        applied = event
        if applied is None and entry.get("_applied_from"):
            applied = _existing_effort_change(turn_key, entry["_applied_from"], target)
        _publish_desktop_decision(
            session_id, provider, model, api_mode, entry, applied=applied)
        if new_request is request:
            return None
        if event is not None:
            try:
                logger.info("Effort changed: %s -> %s", event["from"], event["to"])
            except Exception:
                pass
            _update_cli_status(f"Effort: {event['to']}")
            _notify_cli(f"Effort changed: {event['from']} -> {event['to']}")
        return {
            "request": new_request,
            "source": PLUGIN_ID,
            "reason": f"{mode} {before} -> {target} ({entry.get('label')}, per-message)",
        }
    before = slot[2] if slot is not None else "absent"
    if target == before:
        # The route already sits at the level the scorer picked: nothing to send, so we
        # report no decision at all rather than a rewrite identical to the input.
        _publish_desktop_decision(session_id, provider, model, api_mode, entry)
        return None
    new_request = (_apply(request, slot, target) if slot is not None
                   else _inject(request, injection_path, target))
    if new_request is request:
        _publish_desktop_decision(session_id, provider, model, api_mode, entry)
        return None
    event = _record_effort_change(
        turn_key, before, target, session_id, provider, model, api_mode,
        details=entry)
    applied = event or _existing_effort_change(turn_key, before, target)
    _publish_desktop_decision(
        session_id, provider, model, api_mode, entry, applied=applied)
    if event is not None:
        try:
            logger.info("Effort changed: %s -> %s", event["from"], event["to"])
        except Exception:
            # A logging handler must never turn an optional rewrite into a failure.
            pass
        _update_cli_status(f"Effort: {event['to']}")
        _notify_cli(f"Effort changed: {event['from']} -> {event['to']}")
    return {
        "request": new_request,
        "source": PLUGIN_ID,
        "reason": f"{mode} {before} -> {target} ({entry.get('label')})",
    }
