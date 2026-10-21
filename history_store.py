"""Bounded, prompt-free storage for per-conversation effort history.

The active request worker and the slash-command worker may be different Hermes
processes. SQLite lets both use the same profile-owned plugin data directory
without changing request behavior when storage is unavailable.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

logger = logging.getLogger(__name__)

MAX_HISTORY_CHANGES = 64
MAX_HISTORY_CONVERSATIONS = 64
MAX_MODE_OVERRIDES = 256
MAX_ROUTE_VERDICTS = 128
_OVERRIDE_MODES = ("auto", "once", "always", "off")

_PATH_PROVIDER: Optional[Callable[[], Any]] = None
# Guards only the in-memory caches below; it is never held across SQLite I/O.
_STORE_LOCK = threading.RLock()

# Busy timeout (seconds) on the request path. Zero fails fast: on Windows any non-zero
# SQLite busy wait measured 0.2-0.5 s per blocked statement, which would stall a request.
# History is best-effort, so a contended write is dropped (the request is unaffected).
HOT_BUSY_S = 0.0
COLD_BUSY_S = 0.5
# A short TTL keeps a mode override written by another process visible quickly.
OVERRIDE_TTL_S = 1.0
# An unchanged snapshot is rewritten at most this often (keeps updated_at and LRU fresh).
SNAPSHOT_REFRESH_S = 30.0
_MAX_CACHE_ENTRIES = 512

_ENSURED: set = set()                       # DB paths whose schema was created this process
_OVERRIDE_CACHE: Dict[Any, tuple] = {}      # (path, identities) -> (expires_monotonic, mode)
_SNAPSHOT_CACHE: Dict[Any, tuple] = {}      # (path, scope) -> (fingerprint, monotonic)

_DETAIL_FIELDS = (
    "state", "score", "label", "target", "decision_type", "choices",
    "cache_behavior", "cache_verdict", "continuation", "held", "escalated", "escalation_reason",
    "dynamic", "dynamic_reason", "trial_progress", "mode", "provider", "model", "api_mode",
    "scorer_provider", "scorer_model", "requests", "probes", "elapsed_ms",
    "failure", "updated_at",
)
_STATUS_FIELDS = set(_DETAIL_FIELDS) | {"conversation_id"}
_CACHE_VERDICTS = {"compatible", "sensitive", "not_verified"}


def configure_path_provider(provider: Optional[Callable[[], Any]]) -> None:
    """Use the plugin's host-owned, profile-scoped data directory."""
    global _PATH_PROVIDER
    with _STORE_LOCK:
        _PATH_PROVIDER = provider


def current_identity() -> tuple[Optional[str], List[str]]:
    """Return the durable conversation ID first, then its platform session-key alias."""
    try:
        from gateway.session_context import get_session_env

        session_key = get_session_env("HERMES_SESSION_KEY")
        session_id = get_session_env("HERMES_SESSION_ID")
    except Exception:
        session_key = ""
        session_id = ""
    key = _safe_text(session_key)
    stored_id = _safe_text(session_id)
    scope = stored_id or key
    return scope, list(dict.fromkeys(value for value in (stored_id, key) if value))


def _path() -> Optional[Path]:
    provider = _PATH_PROVIDER
    if not callable(provider):
        return None
    try:
        value = provider()
        return Path(value) if value else None
    except Exception:
        logger.debug("hermes-adaptive-effort: history path unavailable", exc_info=True)
        return None


def _safe_text(value: Any, limit: int = 256) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in value)[:limit]


def _safe_value(key: str, value: Any) -> Any:
    if key == "cache_verdict":
        return value if value in _CACHE_VERDICTS else None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if key == "choices" and isinstance(value, (list, tuple)):
        return [_safe_text(item, 32) for item in value[:16] if isinstance(item, str)]
    if key in _DETAIL_FIELDS or key == "conversation_id":
        return _safe_text(value, 256)
    return None


def _safe_details(details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    return {
        key: _safe_value(key, details.get(key))
        for key in _DETAIL_FIELDS
        if key in details
    }


@contextlib.contextmanager
def _open(path: Path, *, create: bool,
          busy_s: float = COLD_BUSY_S) -> Iterator[sqlite3.Connection]:
    """One committed transaction on a connection that is always closed (Windows file locks)."""
    connection = _connect(path, create=create, busy_s=busy_s)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        scope_id TEXT PRIMARY KEY,
        updated_at REAL NOT NULL,
        latest_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS aliases (
        alias TEXT PRIMARY KEY,
        scope_id TEXT NOT NULL REFERENCES conversations(scope_id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope_id TEXT NOT NULL REFERENCES conversations(scope_id) ON DELETE CASCADE,
        decision_hash TEXT NOT NULL,
        old_effort TEXT NOT NULL,
        new_effort TEXT NOT NULL,
        happened_at REAL NOT NULL,
        provider TEXT,
        model TEXT,
        api_mode TEXT,
        cache_verdict TEXT NOT NULL,
        details_json TEXT NOT NULL,
        UNIQUE(scope_id, decision_hash, old_effort, new_effort)
    );
    CREATE INDEX IF NOT EXISTS changes_recent
        ON changes(scope_id, happened_at DESC, id DESC);
    CREATE TABLE IF NOT EXISTS conversation_modes (
        scope_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS route_verdicts (
        route_key TEXT PRIMARY KEY,
        verdict TEXT NOT NULL,
        detail TEXT,
        updated_at REAL NOT NULL
    );
"""


def _schema_ready(path: Path) -> bool:
    with _STORE_LOCK:
        known = str(path) in _ENSURED
    return known and path.exists()


def _connect(path: Path, *, create: bool,
             busy_s: float = COLD_BUSY_S) -> sqlite3.Connection:
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    needs_schema = create and not _schema_ready(path)
    connection = sqlite3.connect(str(path), timeout=busy_s)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(busy_s * 1000)}")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            # Fewer fsyncs per commit. WAL was measured and rejected: every call opens a
            # connection, and closing the last one checkpoints and deletes the -wal/-shm
            # files, which made the request path slower on Windows than rollback mode.
            connection.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error:
            logger.debug("hermes-adaptive-effort: synchronous pragma failed", exc_info=True)
        if needs_schema:
            connection.executescript(_SCHEMA)
            with _STORE_LOCK:
                _ENSURED.add(str(path))
                for key in [k for k in _SNAPSHOT_CACHE if k[0] == str(path)]:
                    _SNAPSHOT_CACHE.pop(key, None)
    except BaseException:
        connection.close()
        raise
    return connection


def _transact(path: Path, work: Callable[[sqlite3.Connection], Any],
              *, busy_s: float = COLD_BUSY_S) -> Any:
    """Run ``work`` in one write transaction; re-create a vanished schema once."""
    for attempt in (0, 1):
        try:
            with _open(path, create=True, busy_s=busy_s) as connection:
                return work(connection)
        except sqlite3.OperationalError as exc:
            if attempt == 0 and "no such table" in str(exc).lower():
                with _STORE_LOCK:
                    _ENSURED.discard(str(path))
                continue
            raise


def invalidate_caches() -> None:
    """Forget cached overrides and snapshot fingerprints (session reset, tests)."""
    with _STORE_LOCK:
        _OVERRIDE_CACHE.clear()
        _SNAPSHOT_CACHE.clear()


def _bounded_put(cache: Dict[Any, Any], key: Any, value: Any) -> None:
    with _STORE_LOCK:
        if len(cache) >= _MAX_CACHE_ENTRIES and key not in cache:
            cache.clear()
        cache[key] = value


def _aliases(scope_id: Any, aliases: Iterable[Any]) -> List[str]:
    values = [_safe_text(value) for value in (scope_id, *tuple(aliases))]
    return list(dict.fromkeys(value for value in values if value))


def _ensure_conversation(connection: sqlite3.Connection, scope_id: str,
                         at: float, latest: Optional[Dict[str, Any]] = None) -> None:
    encoded = json.dumps(_safe_details(latest), ensure_ascii=False, separators=(",", ":"))
    connection.execute(
        "INSERT INTO conversations(scope_id, updated_at, latest_json) VALUES (?, ?, ?) "
        "ON CONFLICT(scope_id) DO UPDATE SET updated_at=excluded.updated_at, "
        "latest_json=CASE WHEN excluded.latest_json='{}' "
        "THEN conversations.latest_json ELSE excluded.latest_json END",
        (scope_id, at, encoded),
    )


def _bind_aliases(connection: sqlite3.Connection, scope_id: str,
                  aliases: Iterable[Any]) -> None:
    connection.executemany(
        "INSERT INTO aliases(alias, scope_id) VALUES (?, ?) "
        "ON CONFLICT(alias) DO UPDATE SET scope_id=excluded.scope_id",
        [(alias, scope_id) for alias in aliases if alias and alias != scope_id],
    )


def _trim(connection: sqlite3.Connection, scope_id: str) -> None:
    connection.execute("""
        DELETE FROM changes WHERE scope_id=? AND id NOT IN (
            SELECT id FROM changes WHERE scope_id=? ORDER BY happened_at DESC, id DESC LIMIT ?
        )
    """, (scope_id, scope_id, MAX_HISTORY_CHANGES))
    connection.execute("""
        DELETE FROM conversations WHERE scope_id IN (
            SELECT scope_id FROM conversations ORDER BY updated_at DESC LIMIT -1 OFFSET ?
        )
    """, (MAX_HISTORY_CONVERSATIONS,))


def record_snapshot(scope_id: Any, aliases: Iterable[Any],
                    status: Dict[str, Any], at: Optional[float] = None) -> bool:
    """Persist the latest allowlisted decision metadata for one conversation."""
    path = _path()
    scope = _safe_text(scope_id)
    if path is None or not scope:
        return False
    when = float(at if at is not None else time.time())
    safe = {key: _safe_value(key, status.get(key)) for key in _STATUS_FIELDS
            if key in status and key != "conversation_id"}
    safe["updated_at"] = when
    if status.get("cache_verdict") in _CACHE_VERDICTS:
        safe["cache_verdict"] = status["cache_verdict"]
    alias_list = _aliases(scope, aliases)
    fingerprint = json.dumps(
        [{k: v for k, v in safe.items() if k != "updated_at"}, alias_list],
        sort_keys=True, ensure_ascii=False, default=str)
    cache_key = (str(path), scope)
    now = time.monotonic()
    with _STORE_LOCK:
        last = _SNAPSHOT_CACHE.get(cache_key)
    if (last is not None and last[0] == fingerprint
            and now - last[1] < SNAPSHOT_REFRESH_S and _schema_ready(path)):
        return True

    def work(connection: sqlite3.Connection) -> None:
        _ensure_conversation(connection, scope, when, safe)
        _bind_aliases(connection, scope, alias_list)
        _trim(connection, scope)

    try:
        _transact(path, work, busy_s=HOT_BUSY_S)
        _bounded_put(_SNAPSHOT_CACHE, cache_key, (fingerprint, now))
        return True
    except Exception:
        with _STORE_LOCK:
            _SNAPSHOT_CACHE.pop(cache_key, None)
        logger.debug("hermes-adaptive-effort: could not save history status", exc_info=True)
        return False


def record_change(scope_id: Any, aliases: Iterable[Any], decision_key: Any,
                  old_effort: Any, new_effort: Any, provider: Any, model: Any,
                  api_mode: Any, cache_verdict: Any,
                  details: Optional[Dict[str, Any]] = None,
                  at: Optional[float] = None) -> bool:
    """Persist one actual rewrite; repeated tool-loop writes remain deduplicated."""
    path = _path()
    scope = _safe_text(scope_id)
    old_value, new_value = _safe_text(old_effort, 64), _safe_text(new_effort, 64)
    if path is None or not scope or not old_value or not new_value:
        return False
    verdict = str(cache_verdict) if cache_verdict in _CACHE_VERDICTS else "not_verified"
    when = float(at if at is not None else time.time())
    key_hash = hashlib.sha256(str(decision_key).encode("utf-8", errors="replace")).hexdigest()
    safe = _safe_details(details)
    alias_list = _aliases(scope, aliases)
    encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))

    def work(connection: sqlite3.Connection) -> None:
        _ensure_conversation(connection, scope, when)
        _bind_aliases(connection, scope, alias_list)
        connection.execute("""
            INSERT OR IGNORE INTO changes(
                scope_id, decision_hash, old_effort, new_effort, happened_at,
                provider, model, api_mode, cache_verdict, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scope, key_hash, old_value, new_value, when,
            _safe_text(provider), _safe_text(model), _safe_text(api_mode), verdict, encoded,
        ))
        _trim(connection, scope)

    try:
        _transact(path, work, busy_s=HOT_BUSY_S)
        return True
    except Exception:
        logger.debug("hermes-adaptive-effort: could not save applied change", exc_info=True)
        return False


def _resolve_scope(connection: sqlite3.Connection, identities: Iterable[Any]) -> Optional[str]:
    for value in identities:
        alias = _safe_text(value)
        if alias:
            row = connection.execute("SELECT scope_id FROM aliases WHERE alias=?", (alias,)).fetchone()
            if row:
                return str(row[0])
            row = connection.execute(
                "SELECT scope_id FROM conversations WHERE scope_id=?", (alias,)).fetchone()
            if row:
                return str(row[0])
    return None


def read_history(identities: Iterable[Any], limit: int = 10) -> Dict[str, Any]:
    """Read one conversation's changes, never falling back to a process-wide latest row."""
    path = _path()
    if path is None:
        return {"available": False, "scope_id": None, "events": [], "latest": None, "status": {}}
    if not path.is_file():
        return {"available": True, "scope_id": None, "events": [], "latest": None, "status": {}}
    try:
        with _open(path, create=False) as connection:
            scope = _resolve_scope(connection, identities)
            if scope is None:
                return {"available": True, "scope_id": None,
                        "events": [], "latest": None, "status": {}}
            conv = connection.execute(
                "SELECT latest_json FROM conversations WHERE scope_id=?", (scope,)).fetchone()
            count = max(1, min(MAX_HISTORY_CHANGES, int(limit)))
            rows = connection.execute("""
                SELECT id, happened_at, old_effort, new_effort, provider, model,
                       api_mode, cache_verdict, details_json
                FROM changes WHERE scope_id=? ORDER BY happened_at DESC, id DESC LIMIT ?
            """, (scope, count)).fetchall()
            events = []
            for row in rows:
                events.append({
                    "id": int(row["id"]), "at": float(row["happened_at"]),
                    "from": str(row["old_effort"]), "to": str(row["new_effort"]),
                    "provider": row["provider"], "model": row["model"],
                    "api_mode": row["api_mode"], "cache_verdict": row["cache_verdict"],
                    "details": json.loads(row["details_json"]),
                })
            return {
                "available": True, "scope_id": scope,
                "events": events, "latest": events[0] if events else None,
                "status": json.loads(conv["latest_json"]) if conv else {},
            }
    except Exception:
        logger.debug("hermes-adaptive-effort: could not read applied history", exc_info=True)
        return {"available": False, "scope_id": None, "events": [], "latest": None, "status": {}}


def _delete_modes(connection: sqlite3.Connection, identities: Iterable[Any],
                  scope: Optional[str]) -> None:
    """Drop the override rows a reset conversation owned (older files may lack the table)."""
    keys = [value for value in (scope, *(_safe_text(item) for item in identities)) if value]
    try:
        connection.executemany("DELETE FROM conversation_modes WHERE scope_id=?",
                               [(key,) for key in dict.fromkeys(keys)])
    except sqlite3.OperationalError:
        pass


def read_mode_override(identities: Iterable[Any]) -> Optional[str]:
    """Return the per-conversation mode override for the first matching identity, else None.

    Successful lookups (including "no override") are cached per identity tuple for
    ``OVERRIDE_TTL_S``; local writes and resets invalidate the cache at once.
    """
    path = _path()
    if path is None or not path.is_file():
        return None
    keys = tuple(k for k in (_safe_text(v) for v in identities) if k)
    cache_key = (str(path), keys)
    now = time.monotonic()
    with _STORE_LOCK:
        cached = _OVERRIDE_CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        result: Optional[str] = None
        with _open(path, create=False, busy_s=HOT_BUSY_S) as connection:
            for key in keys:
                row = connection.execute(
                    "SELECT mode FROM conversation_modes WHERE scope_id=?", (key,)).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT m.mode FROM aliases a JOIN conversation_modes m "
                        "ON m.scope_id=a.scope_id WHERE a.alias=?", (key,)).fetchone()
                if row is not None:
                    mode = str(row[0])
                    result = mode if mode in _OVERRIDE_MODES else None
                    break
        _bounded_put(_OVERRIDE_CACHE, cache_key, (now + OVERRIDE_TTL_S, result))
        return result
    except Exception:
        logger.debug("hermes-adaptive-effort: could not read mode override", exc_info=True)
        # A contended or broken read keeps the last known value rather than flipping modes.
        return cached[1] if cached is not None else None


def write_mode_override(scope_id: Any, mode: Optional[str]) -> bool:
    """Upsert (or, with mode None, delete) one conversation's override; the caller validates."""
    path = _path()
    scope = _safe_text(scope_id)
    if path is None or not scope:
        return False
    if mode is not None and mode not in _OVERRIDE_MODES:
        return False
    def work(connection: sqlite3.Connection) -> None:
        if mode is None:
            connection.execute("DELETE FROM conversation_modes WHERE scope_id=?", (scope,))
        else:
            connection.execute(
                "INSERT INTO conversation_modes(scope_id, mode, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(scope_id) DO UPDATE SET mode=excluded.mode, "
                "updated_at=excluded.updated_at", (scope, mode, time.time()))
            connection.execute("""
                DELETE FROM conversation_modes WHERE scope_id IN (
                    SELECT scope_id FROM conversation_modes
                    ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?
                )
            """, (MAX_MODE_OVERRIDES,))

    try:
        _transact(path, work)
        return True
    except Exception:
        logger.debug("hermes-adaptive-effort: could not save mode override", exc_info=True)
        return False
    finally:
        invalidate_caches()


def clear_conversation(identities: Iterable[Any]) -> bool:
    """Delete one reset conversation and all of its aliases/history."""
    path = _path()
    if path is None or not path.is_file():
        return path is not None
    try:
        with _open(path, create=False) as connection:
            identities = list(identities)
            scope = _resolve_scope(connection, identities)
            _delete_modes(connection, identities, scope)
            if scope is not None:
                connection.execute("DELETE FROM conversations WHERE scope_id=?", (scope,))
        return True
    except Exception:
        logger.debug("hermes-adaptive-effort: could not clear reset history", exc_info=True)
        return False
    finally:
        invalidate_caches()


# ── observed route verdicts (dynamic-route trial) ────────────────────────────

def store_available() -> bool:
    """True when a profile data path is bound, i.e. a verdict can be remembered."""
    return _path() is not None


def read_route_verdict(route_key: Any) -> tuple[bool, Optional[Dict[str, Any]]]:
    """``(ok, verdict)`` for one route; ``ok`` is False when the store cannot be read.

    A verdict is ``{"verdict": str, "detail": str | None}``. A missing file or row is
    ``(True, None)``: nothing has been observed yet.
    """
    path = _path()
    key = _safe_text(route_key, 512)
    if path is None or not key:
        return False, None
    if not path.is_file():
        return True, None
    try:
        with _open(path, create=False) as connection:
            row = connection.execute(
                "SELECT verdict, detail FROM route_verdicts WHERE route_key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        # An older file written before this table existed: nothing observed yet.
        return True, None
    except Exception:
        logger.debug("hermes-adaptive-effort: could not read route verdict", exc_info=True)
        return False, None
    if row is None:
        return True, None
    return True, {"verdict": str(row[0]), "detail": row[1]}


def write_route_verdict(route_key: Any, verdict: str, detail: Optional[str] = None) -> bool:
    """Upsert one route verdict, keeping the table bounded; ``False`` on any failure."""
    path = _path()
    key = _safe_text(route_key, 512)
    if path is None or not key or not verdict:
        return False
    def work(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO route_verdicts(route_key, verdict, detail, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(route_key) DO UPDATE SET "
            "verdict=excluded.verdict, detail=excluded.detail, "
            "updated_at=excluded.updated_at",
            (key, _safe_text(verdict, 64), _safe_text(detail, 64), time.time()))
        connection.execute("""
            DELETE FROM route_verdicts WHERE route_key IN (
                SELECT route_key FROM route_verdicts
                ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?
            )
        """, (MAX_ROUTE_VERDICTS,))

    try:
        _transact(path, work)
        return True
    except Exception:
        logger.debug("hermes-adaptive-effort: could not save route verdict", exc_info=True)
        return False
