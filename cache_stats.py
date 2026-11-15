"""Attributable prefix-cache loss after *our own* effort change (diagnostic, stdlib only).

The question this answers, and nothing else: *when the plugin changed the effort between two
turns on this route, did that change cost a cold prefix-cache miss?* Everything the plugin
cannot attribute to its own change is counted in a named exclusion bucket instead of being
folded into the rate — an idle conversation whose cache expired, a new session, a compaction,
a route that was already cold, a response without cache counters.

Thresholds come from :mod:`route_trial` (``LOSS_RATIO``, ``KEPT_RATIO``, ``CACHE_TTL_S``,
``MIN_CACHED_TOKENS``) so the diagnostic and the route trial cannot drift apart.

Storage is the profile plugin-data SQLite the history store already uses
(``history_store._path``/``_transact``, the same connection and pruning style). Rows are
per-day aggregates: ``cache_daily`` keeps no prompt text, no session id, no request id and no
timestamp finer than the day, and is pruned to the last :data:`RETENTION_DAYS` days on every
write. Every entry point fails open: a diagnostic must never touch the request path.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import history_store as _history_store
from . import route_trial as _route_trial

logger = logging.getLogger(__name__)

# ── thresholds, shared with the route trial ─────────────────────────────────
LOSS_RATIO = _route_trial.LOSS_RATIO
KEPT_RATIO = _route_trial.KEPT_RATIO
CACHE_TTL_S = _route_trial.CACHE_TTL_S
MIN_CACHED_TOKENS = _route_trial.MIN_CACHED_TOKENS

#: How many days the table keeps, today included.
RETENTION_DAYS = 7
#: Route-day aggregates are buffered and written at most this often (seconds).
FLUSH_INTERVAL_S = 15.0
#: …or as soon as this many distinct route-day rows are buffered.
MAX_BUFFERED_ROWS = 64
MAX_PENDING_REQUESTS = 64
MAX_TRACKED_ROUTES = 128

#: Every counter the table keeps, in column order (the classification adds the exclusions).
COUNTERS = (
    "requests", "prompt_tokens", "cached_tokens", "changes", "cold_after_change",
    "kept_after_change", "between", "excluded_idle", "excluded_session", "excluded_shrank",
    "excluded_no_counter", "excluded_already_cold",
)
#: Exclusion buckets, checked in this order; the first one that applies wins.
EXCLUSIONS = (
    "excluded_session", "excluded_idle", "excluded_shrank", "excluded_no_counter",
    "excluded_already_cold",
)

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS cache_daily (
        day TEXT NOT NULL,
        route TEXT NOT NULL,
        requests INTEGER NOT NULL DEFAULT 0,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        changes INTEGER NOT NULL DEFAULT 0,
        cold_after_change INTEGER NOT NULL DEFAULT 0,
        kept_after_change INTEGER NOT NULL DEFAULT 0,
        "between" INTEGER NOT NULL DEFAULT 0,
        excluded_idle INTEGER NOT NULL DEFAULT 0,
        excluded_session INTEGER NOT NULL DEFAULT 0,
        excluded_shrank INTEGER NOT NULL DEFAULT 0,
        excluded_no_counter INTEGER NOT NULL DEFAULT 0,
        excluded_already_cold INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, route)
    )
"""

_lock = threading.RLock()
_now: Callable[[], float] = time.time
#: ``api_request_id -> {"session", "route", "effort", "messages", "previous"}``.
_PENDING: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
#: ``route_key -> {"session", "effort", "messages", "cached", "at"}`` for the last completion.
_LAST: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
#: ``(day, route_key) -> {counter: delta}`` waiting to be written.
_BUFFER: "OrderedDict[Tuple[str, str], Dict[str, int]]" = OrderedDict()
_last_flush = 0.0


# ── helpers ─────────────────────────────────────────────────────────────────

def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _cache_read(usage: Any) -> Optional[int]:
    """The provider's cache-read counter, or ``None`` when the response carried none."""
    if not isinstance(usage, dict):
        return None
    for key in ("cache_read_tokens", "cached_tokens", "prompt_cache_hit_tokens"):
        if key in usage and usage[key] is not None:
            return _int(usage[key])
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        return _int(details["cached_tokens"])
    return None


def message_count(request: Any) -> Optional[int]:
    """How many messages this request carries (Chat ``messages`` or Responses ``input``)."""
    if not isinstance(request, dict):
        return None
    for key in ("messages", "input"):
        value = request.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _route_key(route: Tuple[str, str, str]) -> str:
    return json.dumps(list(route), separators=(",", ":"))


def parse_route_key(value: Any) -> Tuple[str, str, str]:
    """``route_key`` back to ``(provider, model, api_mode)``; unknown text stays as a model."""
    try:
        parsed = json.loads(str(value))
        if isinstance(parsed, list) and len(parsed) == 3:
            return str(parsed[0]), str(parsed[1]), str(parsed[2])
    except Exception:
        pass
    return "", str(value or ""), ""


def _day(now: Optional[float] = None) -> str:
    moment = datetime.fromtimestamp(_now() if now is None else now, tz=timezone.utc)
    return moment.date().isoformat()


def _retention_cutoff(today: str) -> str:
    """First day kept: ``RETENTION_DAYS`` days ending today."""
    try:
        first = date.fromisoformat(today)
    except Exception:
        first = datetime.fromtimestamp(_now(), tz=timezone.utc).date()
    return (first - timedelta(days=RETENTION_DAYS - 1)).isoformat()


# ── classification ──────────────────────────────────────────────────────────

def classify(previous: Any, current: Any) -> Optional[str]:
    """One bucket for a completed request whose applied effort differs from the previous one.

    ``previous`` is the last *completed* request on the same route and ``current`` the one just
    completed; both are ``{"session", "effort", "messages", "cached", "at"}``. The exclusions are
    checked in :data:`EXCLUSIONS` order — session, idle gap, shrinking history, missing previous
    counter, previous read below :data:`MIN_CACHED_TOKENS` — and the surviving change is then
    classified ``cold_after_change`` (< :data:`LOSS_RATIO`), ``kept_after_change``
    (>= :data:`KEPT_RATIO`) or ``between``. ``None`` means nothing to attribute.
    """
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return None
    if previous.get("session") != current.get("session"):
        return "excluded_session"
    try:
        gap = float(_now()) - float(previous.get("at") or 0.0)
    except (TypeError, ValueError):
        return None
    if gap > CACHE_TTL_S:
        return "excluded_idle"
    was_messages, now_messages = previous.get("messages"), current.get("messages")
    if isinstance(was_messages, int) and isinstance(now_messages, int) \
            and now_messages < was_messages:
        return "excluded_shrank"
    if previous.get("cached") is None:
        return "excluded_no_counter"
    baseline = _int(previous.get("cached"))
    if baseline < MIN_CACHED_TOKENS:
        return "excluded_already_cold"
    read = current.get("cached")
    if read is None:
        return None  # this response reported no counter: no evidence either way
    if read < LOSS_RATIO * baseline:
        return "cold_after_change"
    if read >= KEPT_RATIO * baseline:
        return "kept_after_change"
    return "between"


# ── request path ────────────────────────────────────────────────────────────

def note_request(session_id: Any, route: Any, api_request_id: Any, applied_effort: Any,
                 message_count: Any = None) -> None:
    """Remember what one request carried, and which completed request it will be judged against.

    ``route`` is ``(provider, exact model, api_mode)``; ``applied_effort`` is the value the
    request actually carries after this plugin's handling. Fail-open: nothing raises.
    """
    try:
        sid = str(session_id or "")
        request_id = str(api_request_id or "")
        effort = str(applied_effort or "")
        if not sid or not request_id or not effort or not route:
            return
        key = _route_key(tuple(str(part or "") for part in route))
        with _lock:
            previous = dict(_LAST[key]) if key in _LAST else None
            _PENDING[request_id] = {
                "session": sid, "route": key, "effort": effort,
                "messages": message_count if isinstance(message_count, int) else None,
                "previous": previous, "at": _now(),
            }
            _PENDING.move_to_end(request_id)
            while len(_PENDING) > MAX_PENDING_REQUESTS:
                _PENDING.popitem(last=False)
    except Exception:
        logger.debug("hermes-adaptive-effort: cache stats request note failed", exc_info=True)


def on_response(api_request_id: Any, usage: Any, message_count: Any = None) -> Optional[str]:
    """Account one completed request; returns the bucket the change fell into, if any.

    Counters accumulate in memory and are flushed by :func:`flush` (also on the command verb),
    so the response path never opens SQLite.
    """
    try:
        request_id = str(api_request_id or "")
        with _lock:
            pending = _PENDING.pop(request_id, None)
        if pending is None:
            return None
        usage = usage if isinstance(usage, dict) else {}
        cached = _cache_read(usage)
        prompt = _int(usage.get("prompt_tokens")) if "prompt_tokens" in usage else 0
        messages = message_count if isinstance(message_count, int) else pending.get("messages")
        with _lock:
            previous = pending.get("previous")
            changed = bool(previous) and previous.get("effort") != pending["effort"]
            bucket = classify(previous, {"session": pending["session"], "cached": cached,
                                         "messages": messages}) if changed else None
            delta = {"requests": 1}
            if prompt:
                delta["prompt_tokens"] = prompt
            if cached is not None:
                delta["cached_tokens"] = cached
            if changed and bucket in EXCLUSIONS:
                delta[bucket] = 1
            elif bucket in ("cold_after_change", "kept_after_change", "between"):
                delta["changes"] = 1
                delta[bucket] = 1
            _add_locked(_day(), pending["route"], delta)
            # This request is now the baseline for the next one on the route.
            _LAST.pop(pending["route"], None)
            _LAST[pending["route"]] = {
                "session": pending["session"], "effort": pending["effort"],
                "messages": messages if isinstance(messages, int) else pending.get("messages"),
                "cached": cached, "at": _now(),
            }
            while len(_LAST) > MAX_TRACKED_ROUTES:
                _LAST.popitem(last=False)
        return bucket
    except Exception:
        logger.debug("hermes-adaptive-effort: cache stats response failed", exc_info=True)
        return None


def on_error(api_request_id: Any) -> None:
    """A failed request never completed: drop it so it counts for nothing."""
    try:
        with _lock:
            _PENDING.pop(str(api_request_id or ""), None)
    except Exception:
        logger.debug("hermes-adaptive-effort: cache stats error note failed", exc_info=True)


def _add_locked(day: str, route_key: str, delta: Dict[str, int]) -> None:
    key = (day, route_key)
    row = _BUFFER.get(key)
    if row is None:
        row = {}
        _BUFFER[key] = row
    for counter, value in delta.items():
        if counter in COUNTERS and value:
            row[counter] = row.get(counter, 0) + int(value)
    _BUFFER.move_to_end(key)


# ── persistence ─────────────────────────────────────────────────────────────

def _should_flush(now: float) -> bool:
    return bool(_BUFFER) and (len(_BUFFER) >= MAX_BUFFERED_ROWS
                              or now - _last_flush >= FLUSH_INTERVAL_S)


def flush(force: bool = True) -> bool:
    """Write the buffered route-day aggregates and prune the table to the retention window.

    ``force=False`` writes only when the interval or the row bound says so. Returns ``True``
    when the buffer was written (or was already empty).
    """
    try:
        now = _now()
        with _lock:
            if not _BUFFER:
                return True
            if not force and not _should_flush(now):
                return False
            rows = [(day, route, dict(counters)) for (day, route), counters in _BUFFER.items()]
        path = _path()
        if path is None:
            return False
        today = _day(now)
        cutoff = _retention_cutoff(today)
        for day, route_key, counters in rows:
            _write_row(path, day, route_key, counters, cutoff)
        with _lock:
            for key in rows:
                buffered = _BUFFER.get((key[0], key[1]))
                if buffered is not None:
                    for counter, value in key[2].items():
                        buffered[counter] = buffered.get(counter, 0) - value
                    if not any(value > 0 for value in buffered.values()):
                        _BUFFER.pop((key[0], key[1]), None)
            global _last_flush
            _last_flush = now
        return True
    except Exception:
        logger.debug("hermes-adaptive-effort: cache stats flush failed", exc_info=True)
        return False


def _path() -> Any:
    """The profile plugin-data database, via the history store's own path provider."""
    try:
        return _history_store._path()
    except Exception:
        return None


def _columns() -> str:
    """``between`` is a SQL keyword: every counter is quoted so the named column stays exact."""
    return ", ".join(f'"{name}"' for name in COUNTERS)


def _write_row(path: Any, day: str, route_key: str, counters: Dict[str, int],
               cutoff: str) -> None:
    columns = _columns()
    placeholders = ", ".join("?" for _ in COUNTERS)
    updates = ", ".join(f'"{name}"=cache_daily."{name}"+excluded."{name}"' for name in COUNTERS)
    day_cutoff = cutoff

    def work(connection: Any) -> None:
        connection.execute(_SCHEMA)
        connection.execute(
            f"INSERT INTO cache_daily(day, route, {columns}) VALUES (?, ?, {placeholders}) "
            f"ON CONFLICT(day, route) DO UPDATE SET {updates}",
            (day, route_key, *[int(counters.get(name, 0)) for name in COUNTERS]))
        connection.execute("DELETE FROM cache_daily WHERE day < ?", (day_cutoff,))

    _history_store._transact(path, work)


def read_rows(days: int = RETENTION_DAYS) -> List[Dict[str, Any]]:
    """Rows inside the window, oldest first; an unreadable store reads as empty."""
    try:
        path = _path()
        if path is None:
            return []
        today = _day()
        try:
            first = date.fromisoformat(today)
        except Exception:
            first = datetime.fromtimestamp(_now(), tz=timezone.utc).date()
        span = max(1, int(days or RETENTION_DAYS)) - 1
        cutoff = (first - timedelta(days=span)).isoformat()
        if not path.is_file():
            return []
        with _history_store._open(path, create=False) as connection:
            rows = connection.execute(
                f'SELECT day, route, {_columns()} FROM cache_daily '
                "WHERE day >= ? ORDER BY day, route", (cutoff,)).fetchall()
        return [{"day": row[0], "route": parse_route_key(row[1]),
                 **{name: int(row[index + 2] or 0) for index, name in enumerate(COUNTERS)}}
                for row in rows]
    except Exception:
        logger.debug("hermes-adaptive-effort: cache stats read failed", exc_info=True)
        return []


def dominant_exclusion(row: Dict[str, Any]) -> Optional[str]:
    """The largest exclusion bucket on one row, or ``None`` when nothing was excluded."""
    best, best_value = None, 0
    for name in EXCLUSIONS:
        value = int((row or {}).get(name) or 0)
        if value > best_value:
            best, best_value = name, value
    return best


def reset_state() -> None:
    """Drop the in-memory half (pending, baselines, unwritten counters); the table survives."""
    global _last_flush
    with _lock:
        _PENDING.clear()
        _LAST.clear()
        _BUFFER.clear()
        _last_flush = 0.0
