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
value is clamped onto the route's declared vocabulary and re-clamped whenever the
route changes; one Jev call per user TURN (and none at all when the field cannot
be rewritten); no prompt text is stored.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from . import cache_safety as _cache_safety
from . import effort as _effort
from . import jev_client as _jev_client

logger = logging.getLogger(__name__)

PLUGIN_ID = "jev-auto"
VALID_MODES: Tuple[str, ...] = ("off", "recommend", "auto", "cache_safe")
DEFAULTS: Dict[str, Any] = {
    "mode": "off",
    "subagent_mode": "off",
    "timeout_s": _jev_client.DEFAULT_TIMEOUT_S,
    "max_turns": 64,
    "prompt_chars": _jev_client.DEFAULT_MAX_PROMPT_CHARS,
    "endpoint": _jev_client.DEFAULT_ENDPOINT,
}

# Injected by register(ctx); None until a PluginContext exists (and in tests).
_settings_provider: Optional[Callable[[str, Any], Any]] = None
# Injected by tests so classification never reaches the network.
_classifier_factory: Optional[Callable[..., Any]] = None
# Config source seam. ``None`` in production: the plugin reads the operator's
# live profile. Tests inject a hermetic reader so a unit run never depends on
# (and never reads) whatever mode the live profile happens to carry.
_config_reader: Optional[Callable[[], Dict[str, Any]]] = None
# Mode chosen at runtime through ``/jev-auto off|recommend|auto|cache_safe``.
# Process-local by design: the plugin never writes the operator's config file,
# and the command's reply says so. ``None`` means "use the configured mode".
_MODE_OVERRIDE: Optional[str] = None

_lock = threading.Lock()
_SESSIONS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
#: Sessions with a classification running right now — at most one probe per
#: session, claimed atomically so two concurrent requests cannot both call Jev.
_IN_FLIGHT: set = set()
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
    global _MODE_OVERRIDE
    with _lock:
        _SESSIONS.clear()
        _IN_FLIGHT.clear()
        _CHILD_GOALS.clear()
        _MODE_OVERRIDE = None


# ── runtime mode override (``/jev-auto off|recommend|auto|cache_safe``) ──────

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
    turn still reuses a single decision — and therefore a single Jev call.
    """
    turn = str(turn_id or "").strip()
    return f"{session_id}/{turn}" if turn else str(session_id)


def _session_of(key: str) -> str:
    """The session a decision key belongs to (used by per-session cleanup)."""
    return key.split("/", 1)[0]


def session_state() -> Dict[str, Dict[str, Any]]:
    """Snapshot of stored decisions: effort metadata only, never prompt text."""
    with _lock:
        return {k: dict(v) for k, v in _SESSIONS.items()}


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


def _touch(key: str, settings: Dict[str, Any], mode: str,
           provider: Any = None, model: Any = None) -> Dict[str, Any]:
    """One turn's record: created once, then counted and stamped here.

    Every request that reaches the decision path is counted (``requests``), while
    ``probes`` only ever grows inside the claim — that difference is exactly what
    ``/jev-auto status`` reports as "one Jev call per turn".
    """
    with _lock:
        entry = _SESSIONS.get(key)
        if entry is None:
            entry = {
                "state": "new", "label": None, "target": None, "score": None,
                "mode": mode, "provider": provider, "model": model,
                "requests": 0, "probes": 0, "elapsed_ms": 0.0,
                "failure": None, "updated_at": 0.0,
            }
            _SESSIONS[key] = entry
        entry["requests"] = int(entry.get("requests") or 0) + 1
        entry["mode"] = mode
        entry["updated_at"] = time.time()
        _SESSIONS.move_to_end(key)
        while len(_SESSIONS) > max(1, int(settings["max_turns"])):
            _SESSIONS.popitem(last=False)
    return entry


def on_session_end(session_id: Optional[str] = None, **kwargs: Any) -> None:
    """Verified hook: clears the session's decision (in-memory only)."""
    if not session_id:
        return
    session = str(session_id)
    with _lock:
        # Every turn of that session, plus the bare key used when no turn id was
        # available: ending a session must not leave decisions behind.
        for key in [k for k in _SESSIONS if _session_of(k) == session]:
            _SESSIONS.pop(key, None)
            _IN_FLIGHT.discard(key)
        _IN_FLIGHT.discard(session)
        _CHILD_GOALS.pop(session, None)


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
    profile: a profile carrying ``mode: auto`` used to make this plugin's own
    "the default is off" tests fail once the Hermes core was importable. In
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
    """``plugins.entries.jev-auto.settings.<key>``, via ctx when present."""
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


def _settings() -> Dict[str, Any]:
    mode = str(_read_setting("mode", DEFAULTS["mode"]) or "").strip().lower()
    mode_source = "config"
    if mode not in VALID_MODES:
        mode = "off"
    override = mode_override()
    if override is not None:
        # A runtime choice outranks the file: /jev-auto just told the operator it
        # applies to future requests, so it must.
        mode, mode_source = override, "override"
    # Independent gate: children are routed only when BOTH the session mode and
    # this one allow it, so opting into session routing never silently starts
    # rewriting subagent effort.
    subagent_mode = str(_read_setting("subagent_mode", DEFAULTS["subagent_mode"]) or "").strip().lower()
    if subagent_mode not in VALID_MODES:
        subagent_mode = "off"

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
        "mode_source": mode_source,
        "subagent_mode": subagent_mode,
        "timeout_s": _num("timeout_s", DEFAULTS["timeout_s"]),
        "max_turns": _int("max_turns", DEFAULTS["max_turns"]),
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


def _first_responses_text(input_items: Any) -> Optional[str]:
    """First user text of a *preflighted* ``codex_responses`` payload.

    On that route the middleware sees the payload AFTER
    ``agent._get_transport().preflight_kwargs()`` has replaced ``messages`` with
    ``input`` (``codex_responses_adapter._preflight_codex_api_kwargs``), so
    ``_first_user_text(request["messages"])`` is always ``None`` and every normal
    Codex session was a silent no-op — only subagents, classified from the goal
    their parent wrote, ever worked.
    """
    if not isinstance(input_items, list):
        return None
    for item in input_items:
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


def _first_user_prompt(request: Dict[str, Any]) -> Optional[str]:
    """Classification target of a request in any of the shapes we can see.

    Chat Completions carries ``messages``; a preflighted Codex payload carries
    ``input`` instead. Read both so the decision does not depend on the api_mode.
    """
    prompt = _first_user_text(request.get("messages"))
    if prompt:
        return prompt
    return _first_responses_text(request.get("input"))


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
    # codex_responses (and the Responses family generally) carries effort in a
    # TOP-LEVEL ``reasoning`` object — verified live against ResponsesApiTransport:
    # build_kwargs() returns reasoning={"effort":…,"summary":…} with extra_body
    # absent. Without this branch every subagent on our delegation route would be
    # a silent no-op, because the parent session never uses it.
    reasoning = request.get("reasoning")
    if isinstance(reasoning, dict):
        value = reasoning.get("effort")
        if (isinstance(value, str) and value.strip()
                and value.strip().lower() != "none"):
            return reasoning, "effort", value.strip().lower()
    return None


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

def _target_for_route(entry: Dict[str, Any], provider: Any, model: Any) -> Optional[str]:
    """The wire target *entry* gets on the CURRENT route, or ``None``.

    A stored target is only legal for the route that produced it. A provider
    fallback inside one turn — or ``cache_safe`` pinning a session and then
    seeing the route change — keeps the same decision key while the route
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
    if (entry.get("provider"), entry.get("model")) == (provider, model):
        target = entry.get("target")
        return target if isinstance(target, str) and target else None
    target = _effort.map_effort(label, provider, model)
    if target is None:
        return None
    entry["target"] = target
    entry["provider"] = provider
    entry["model"] = model
    return target


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

    # One decision per USER TURN, not per session: a "hey" opening a long
    # session must not freeze `low` onto every later question. turn_id is minted
    # per user message, so a tool loop inside one turn still reuses its decision
    # and therefore still costs a single Jev call.
    key = _decision_key(session_id, kwargs.get("turn_id"))

    # `cache_safe` is per-turn routing where the route survives an effort change,
    # and session-pinned routing where it does not. Verified per api_mode by
    # cache_safety.effort_is_cache_safe(); an unknown route pins, never gambles.
    if mode == "cache_safe":
        safe = _cache_safety.effort_is_cache_safe(
            provider, model, kwargs.get("api_mode"))
        if not safe:
            key = _decision_key(session_id, None)

    # A subagent is classified from the goal its PARENT wrote, not from its own
    # first prompt. subagent_mode is a second, independent gate: the session mode
    # alone never starts rewriting children's effort.
    child_goal = _child_goal(session_id)
    if child_goal is not None and settings["subagent_mode"] == "off":
        return None
    if child_goal is not None:
        mode = settings["subagent_mode"]

    entry = _touch(key, settings, mode, provider, model)
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
        # A subagent classifies the terse goal its parent wrote; a normal session
        # classifies its own first user message.
        prompt = child_goal if child_goal is not None else _first_user_prompt(request)
        if not prompt:
            entry["state"] = "unsupported"
            return None
        if not _claim(key):
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
            # Record the route that produced this target: the value is only legal
            # for it, and every later request re-checks the route it arrives on.
            entry["target"] = target
            entry["provider"] = provider
            entry["model"] = model
            entry["state"] = "decided"
        finally:
            _release(key)

    target = _target_for_route(entry, provider, model)
    if target is None:
        if entry.get("state") == "decided":
            # The decision stands for the prompt, but THIS route has no level
            # for it (a narrower route mid-turn). Rewriting nothing is the only
            # safe answer, and the label is not re-classified in this turn.
            entry["state"] = "unsupported"
        return None
    if target == slot[2]:
        # The route already sits at the level Jev picked: nothing to send, so we
        # report no decision at all rather than a rewrite identical to the input.
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
