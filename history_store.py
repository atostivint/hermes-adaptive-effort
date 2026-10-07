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

_PATH_PROVIDER: Optional[Callable[[], Any]] = None
_STORE_LOCK = threading.RLock()

_DETAIL_FIELDS = (
    "state", "score", "label", "target", "decision_type", "choices",
    "cache_behavior", "cache_verdict", "mode", "provider", "model", "api_mode",
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
def _open(path: Path, *, create: bool) -> Iterator[sqlite3.Connection]:
    """One committed transaction on a connection that is always closed (Windows file locks)."""
    connection = _connect(path, create=create)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _connect(path: Path, *, create: bool) -> sqlite3.Connection:
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=0.5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 500")
        connection.execute("PRAGMA foreign_keys = ON")
        if create:
            connection.executescript("""
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
            """)
    except BaseException:
        connection.close()
        raise
    return connection


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
    try:
        with _STORE_LOCK, _open(path, create=True) as connection:
            _ensure_conversation(connection, scope, when, safe)
            _bind_aliases(connection, scope, _aliases(scope, aliases))
            _trim(connection, scope)
        return True
    except Exception:
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
    try:
        with _STORE_LOCK, _open(path, create=True) as connection:
            _ensure_conversation(connection, scope, when)
            _bind_aliases(connection, scope, _aliases(scope, aliases))
            connection.execute("""
                INSERT OR IGNORE INTO changes(
                    scope_id, decision_hash, old_effort, new_effort, happened_at,
                    provider, model, api_mode, cache_verdict, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scope, key_hash, old_value, new_value, when,
                _safe_text(provider), _safe_text(model), _safe_text(api_mode), verdict,
                json.dumps(safe, ensure_ascii=False, separators=(",", ":")),
            ))
            _trim(connection, scope)
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
        with _STORE_LOCK, _open(path, create=False) as connection:
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


def clear_conversation(identities: Iterable[Any]) -> bool:
    """Delete one reset conversation and all of its aliases/history."""
    path = _path()
    if path is None or not path.is_file():
        return path is not None
    try:
        with _STORE_LOCK, _open(path, create=False) as connection:
            scope = _resolve_scope(connection, identities)
            if scope is not None:
                connection.execute("DELETE FROM conversations WHERE scope_id=?", (scope,))
        return True
    except Exception:
        logger.debug("hermes-adaptive-effort: could not clear reset history", exc_info=True)
        return False
