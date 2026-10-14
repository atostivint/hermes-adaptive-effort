"""Bounded trial of unverified routes: does a changing effort keep the provider's prompt cache?

``auto`` keeps one decision per route unless the route is in the exact registry
(``middleware._dynamic_effort_route``). On a cache-safe, unlisted route with an existing
effort field this module lets ``auto`` change effort every turn *on trial* while the
``post_api_request`` / ``api_request_error`` hooks feed it the provider's own cache counters.
Three effort changes that keep the cache grant dynamic status (``observed``); a clear drop right
after a change pins the route again (``observed_cache_loss``). The worst case is one cache miss.

Everything here is heuristic and the thresholds below are the only place they live. Only token
counts, effort values and a request correlation id are kept, never text. Every entry point
fails open: an error leaves requests unchanged and the route pinned.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from . import cache_safety as _cache_safety
from . import history_store as _history_store

logger = logging.getLogger(__name__)

# ── heuristic thresholds ─────────────────────────────────────────────────────

#: Effort changes that must keep the cache before a route is ``observed``.
SUCCESSES_REQUIRED = 3
#: Cache read after a change at or above this share of the previous request's read: kept.
KEPT_RATIO = 0.50
#: Cache read after a change below this share of the previous request's read: a clear loss.
LOSS_RATIO = 0.10
#: Smallest previous cache read (tokens) that counts as a meaningful baseline.
MIN_CACHED_TOKENS = 256
#: Longer than this between two requests and the provider cache may simply have expired.
CACHE_TTL_S = 300.0
#: Prompt shrinking below this share of the previous prompt means compression / a history rewrite.
HISTORY_REWRITE_RATIO = 0.80
#: Consecutive responses without any cache counter before the route is ``no_cache_data``.
NO_DATA_LIMIT = 5
#: Provider statuses that reject the request body rather than the account or the load.
REJECTION_STATUSES = frozenset({400, 422})
#: In-memory bounds.
MAX_TRACKED_SESSIONS = 64
MAX_PENDING_REQUESTS = 64
MAX_TRACKED_ROUTES = 128

# ── stable codes ─────────────────────────────────────────────────────────────

LISTED = "listed"
OBSERVED = "observed"
TRIAL = "trial"
OBSERVED_CACHE_LOSS = "observed_cache_loss"
REJECTED_LEVEL = "rejected_level"
NO_CACHE_DATA = "no_cache_data"
API_MODE = "api_mode"
TRIAL_DISABLED = "trial_disabled"

#: Verdicts that pin a route; ``OBSERVED`` is the only one that grants dynamic status.
NEGATIVE_VERDICTS = frozenset({OBSERVED_CACHE_LOSS, REJECTED_LEVEL, NO_CACHE_DATA})
PERSISTED_VERDICTS = NEGATIVE_VERDICTS | {OBSERVED}

_lock = threading.RLock()
#: route key -> {"successes", "no_data", "verdict", "detail"}; verdict is loaded lazily.
_ROUTES: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
#: (session, route key) -> counts of the previous request; no text.
_SESSIONS: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
#: api_request_id -> what the middleware sent; consumed by the response/error hook.
_PENDING: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
#: Clock seam (epoch seconds); tests replace it.
_now: Callable[[], float] = time.time


def reset() -> None:
    """Drop in-memory counters; persisted verdicts stay and are re-read on demand."""
    with _lock:
        _ROUTES.clear()
        _SESSIONS.clear()
        _PENDING.clear()


def clear_session(session_id: Any) -> None:
    sid = str(session_id or "")
    with _lock:
        for key in [key for key in _SESSIONS if key[0] == sid]:
            _SESSIONS.pop(key, None)
        for key in [key for key, item in _PENDING.items() if item.get("session") == sid]:
            _PENDING.pop(key, None)


def route_key(route: Tuple[str, str, str]) -> str:
    return json.dumps(list(route), separators=(",", ":"))


def eligible_route(route: Tuple[str, str, str], route_choices: Any) -> bool:
    """A route the trial may run on: cache-safe API mode, not Claude, no registry vocabulary."""
    provider, model, api = route
    return (bool(model) and provider != "anthropic" and not route_choices
            and _cache_safety.effort_is_cache_safe(provider, model, api))


def _record(key: str) -> Dict[str, Any]:
    """The route's counters, loading a persisted verdict the first time it is needed."""
    with _lock:
        record = _ROUTES.get(key)
        if record is not None:
            _ROUTES.move_to_end(key)
            return record
    ok, stored = _history_store.read_route_verdict(key)
    record = {"successes": 0, "no_data": 0, "verdict": None, "detail": None, "store_ok": ok}
    if stored and stored.get("verdict") in PERSISTED_VERDICTS:
        record["verdict"] = stored["verdict"]
        record["detail"] = stored.get("detail")
    with _lock:
        existing = _ROUTES.get(key)
        if existing is not None:
            return existing
        _ROUTES[key] = record
        while len(_ROUTES) > MAX_TRACKED_ROUTES:
            _ROUTES.popitem(last=False)
    return record


def status(route: Tuple[str, str, str], route_choices: Any = (),
           has_effort_field: bool = True) -> Dict[str, Any]:
    """``{"dynamic", "dynamic_reason", "trial_progress"}`` for an unlisted route."""
    provider, _model, api = route
    if provider == "anthropic" or not _cache_safety.effort_is_cache_safe(provider, _model, api):
        return {"dynamic": False, "dynamic_reason": API_MODE, "trial_progress": None}
    if not eligible_route(route, route_choices):
        return {"dynamic": False, "dynamic_reason": TRIAL_DISABLED, "trial_progress": None}
    record = _record(route_key(route))
    verdict = record.get("verdict")
    if verdict in NEGATIVE_VERDICTS:
        return {"dynamic": False, "dynamic_reason": verdict, "trial_progress": None}
    if not record.get("store_ok") or record.get("store_failed"):
        # No place to remember a verdict: stay pinned, exactly like before the trial existed.
        return {"dynamic": False, "dynamic_reason": TRIAL_DISABLED, "trial_progress": None}
    if verdict == OBSERVED:
        return {"dynamic": True, "dynamic_reason": OBSERVED, "trial_progress": None}
    if not has_effort_field:
        return {"dynamic": None, "dynamic_reason": None, "trial_progress": None}
    return {"dynamic": True, "dynamic_reason": TRIAL,
            "trial_progress": int(record.get("successes") or 0)}


def level_rejected(route: Tuple[str, str, str], level: Any) -> bool:
    """True when the provider rejected *level* on this route during a trial."""
    record = _record(route_key(route))
    return record.get("verdict") == REJECTED_LEVEL and record.get("detail") == str(level)


def _finalize(key: str, record: Dict[str, Any], verdict: str, detail: Optional[str] = None) -> None:
    saved = _history_store.write_route_verdict(key, verdict, detail)
    with _lock:
        if saved or verdict in NEGATIVE_VERDICTS:
            # A pinning verdict is safe to hold in memory even when it cannot be saved.
            record["verdict"] = verdict
            record["detail"] = detail
        else:
            # A positive verdict nobody can remember is not granted: stay pinned.
            record["store_failed"] = True
        record["successes"] = 0
        record["no_data"] = 0


def note_request(session_id: Any, route: Tuple[str, str, str], api_request_id: Any,
                 sent_effort: Any, rewritten: bool) -> None:
    """Remember the effort one trial request carried and whether it changed."""
    sid, request_id, level = str(session_id or ""), str(api_request_id or ""), str(sent_effort or "")
    if not sid or not request_id or not level:
        return
    key = route_key(route)
    with _lock:
        state = _SESSIONS.get((sid, key))
        previous = state.get("effort") if state else None
        if state is None:
            state = {}
            _SESSIONS[(sid, key)] = state
        _SESSIONS.move_to_end((sid, key))
        while len(_SESSIONS) > MAX_TRACKED_SESSIONS:
            _SESSIONS.popitem(last=False)
        changed = previous is not None and previous != level
        state["effort"] = level
        _PENDING[request_id] = {"session": sid, "route": key, "changed": changed,
                                "level": level, "rewritten": bool(rewritten)}
        while len(_PENDING) > MAX_PENDING_REQUESTS:
            _PENDING.popitem(last=False)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def on_response(api_request_id: Any, usage: Any, message_count: Any = None) -> Optional[str]:
    """Judge one provider response; returns the verdict reached by it, if any."""
    with _lock:
        pending = _PENDING.pop(str(api_request_id or ""), None)
    if pending is None:
        return None
    key, sid = pending["route"], pending["session"]
    record = _record(key)
    if record.get("verdict") is not None or record.get("store_failed"):
        return None
    usage = usage if isinstance(usage, dict) else {}
    cache_read = _int(usage.get("cache_read_tokens"))
    cache_write = _int(usage.get("cache_write_tokens"))
    prompt = _int(usage.get("prompt_tokens"))
    messages = _int(message_count)
    now = _now()

    with _lock:
        if cache_read == 0 and cache_write == 0:
            record["no_data"] = int(record.get("no_data") or 0) + 1
        else:
            record["no_data"] = 0
        no_data = record["no_data"] >= NO_DATA_LIMIT
    if no_data:
        _finalize(key, record, NO_CACHE_DATA)
        return NO_CACHE_DATA

    with _lock:
        state = _SESSIONS.get((sid, key))
        if state is None:
            return None
        before = {name: state.get(name) for name in ("read", "prompt", "messages", "at")}
        state.update(read=cache_read, prompt=prompt, messages=messages, at=now)
        previous_read = _int(before["read"])
        # Only a comparison against a meaningful earlier read, soon enough, on an unrewritten
        # history, says anything about the cache.
        conclusive = (
            before["at"] is not None
            and previous_read >= MIN_CACHED_TOKENS
            and now - float(before["at"]) <= CACHE_TTL_S
            and not (messages and _int(before["messages"]) and messages < _int(before["messages"]))
            and not (_int(before["prompt"]) and prompt < HISTORY_REWRITE_RATIO * _int(before["prompt"]))
        )
        if not conclusive:
            return None
        kept = cache_read >= KEPT_RATIO * previous_read
        if not pending["changed"]:
            # A plain continuation that kept its cache proves this session's baseline works,
            # so a later drop is the effort change's doing and not an expired cache.
            if kept:
                state["baseline_ok"] = True
            return None
        if kept:
            record["successes"] = int(record.get("successes") or 0) + 1
            reached = record["successes"] >= SUCCESSES_REQUIRED
            lost = False
        else:
            lost = cache_read < LOSS_RATIO * previous_read and bool(state.get("baseline_ok"))
            reached = False
    if lost:
        _finalize(key, record, OBSERVED_CACHE_LOSS)
        return OBSERVED_CACHE_LOSS
    if reached:
        _finalize(key, record, OBSERVED)
        return OBSERVED if record.get("verdict") == OBSERVED else None
    return None


def on_error(api_request_id: Any, status_code: Any) -> Optional[str]:
    """A 4xx body rejection of a request this plugin rewrote pins the route and bans the level."""
    with _lock:
        pending = _PENDING.pop(str(api_request_id or ""), None)
    if pending is None or not pending.get("rewritten"):
        return None
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return None
    if code not in REJECTION_STATUSES:
        return None
    record = _record(pending["route"])
    if record.get("verdict") is not None:
        return None
    _finalize(pending["route"], record, REJECTED_LEVEL, pending["level"])
    return REJECTED_LEVEL
