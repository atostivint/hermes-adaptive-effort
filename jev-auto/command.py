"""/jev-auto — explain and inspect the plugin (read-only by design).

This command never classifies, never rewrites a request, and never stores
anything: it only renders the state the middleware already holds. It accepts
no arguments after the verb — and since every command runs through the plugin
dispatcher's argument split, `handle()` receives a string, not a list.

Schemas below are part of the documented contract (`jev-auto.*.v1`): the keys
are stable, and no prompt text ever leaves this module beyond the text the
operator typed themselves for a probe.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from . import jev_client as _jev_client
from . import middleware as _middleware

PLUGIN_ID = _middleware.PLUGIN_ID
USAGE = """Usage:
  /jev-auto status      Show mode, settings, credential, session counts
  /jev-auto status json Machine-readable status payload
  /jev-auto probe <text>  Classify <text> once (prints score/label, stores nothing)
  /jev-auto help        Show this help

Modes: off | recommend | auto. Re-run /jev-auto setup to change mode.
Probe asks Jev for a rubric score on the text you typed — it does not use or
store your session's conversation, and never writes to session state."""

STATUS_SCHEMA = "jev-auto.status.v1"
PROBE_SCHEMA = "jev-auto.probe.v1"

#: The only session fields ever rendered — an entry may hold anything (it is
#: plugin-author payload), so prompt text and provider junk are filtered out.
_ENTRY_FIELDS = (
    "state", "score", "label", "target", "mode", "requests", "probes",
    "elapsed_ms", "failure", "updated_at",
)


def handle(raw_args: str) -> str:
    """Dispatcher entry point. Signature must match a PluginCommand's handler."""
    try:
        return _dispatch(raw_args)
    except Exception:
        # Output rules are a contract: even on a rendering bug we return usage
        # rather than letting an exception bubble into the chat.
        return USAGE


def _dispatch(raw_args: str) -> str:
    parts = (raw_args or "").split()
    if not parts:
        return USAGE
    verb, *rest = parts
    if verb in {"help", "-h", "--help"}:
        return USAGE
    if verb == "status":
        if not rest:
            return _status_text()
        if rest == ["json"]:
            return _status_json()
        return USAGE
    if verb == "probe":
        if not rest:
            return USAGE
        return _probe(" ".join(rest))
    return USAGE


# ── status ──────────────────────────────────────────────────────────────────

def _public_entry(session_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"session_id": str(session_id)}
    for key in _ENTRY_FIELDS:
        out[key] = entry.get(key)
    return out


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _last_session(sessions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Most recently updated entry; ties go to the last one in the list."""
    best: Optional[Dict[str, Any]] = None
    best_key = (-1.0, -1)
    for index, entry in enumerate(sessions):
        key = (_number(entry.get("updated_at")), index)
        if key >= best_key:
            best_key, best = key, entry
    return best


def _status_payload() -> Dict[str, Any]:
    settings = _middleware._settings()
    sessions = [_public_entry(sid, entry)
                for sid, entry in _middleware.session_state().items()]
    counts = {
        "sessions": len(sessions),
        "in_flight": len(_middleware.in_flight()),
        "requests": sum(int(_number(e.get("requests"))) for e in sessions),
        "probes": sum(int(_number(e.get("probes"))) for e in sessions),
        "decided": sum(1 for e in sessions if e.get("state") == "decided"),
        "failed": sum(1 for e in sessions if e.get("state") == "failed"),
        "unsupported": sum(1 for e in sessions if e.get("state") == "unsupported"),
    }
    return {
        "schema": STATUS_SCHEMA,
        "plugin": PLUGIN_ID,
        "mode": settings["mode"],
        "settings": settings,
        "credential": _jev_client.credential_present(),
        "counts": counts,
        "sessions": sessions,
        "last": _last_session(sessions),
    }


def _render_entry(entry: Dict[str, Any]) -> str:
    state = entry.get("state")
    parts = [f"session={entry['session_id']}", f"state={state}"]
    for key in ("score", "label", "target", "requests", "probes", "elapsed_ms",
                "failure"):
        value = entry.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return "  " + " ".join(parts)


def _status_text() -> str:
    payload = _status_payload()
    settings = payload["settings"]
    counts = payload["counts"]
    lines = [
        "jev-auto status",
        f"mode: {payload['mode']}",
        f"credential: {'present' if payload['credential'] else 'missing'}",
        f"settings: timeout_s={settings['timeout_s']} "
        f"max_turns={settings['max_turns']} "
        f"max_prompt_chars={settings['prompt_chars']}",
        f"endpoint: {settings['endpoint']}",
        f"counts: sessions={counts['sessions']} in_flight={counts['in_flight']} "
        f"requests={counts['requests']} probes={counts['probes']} "
        f"decided={counts['decided']} failed={counts['failed']} "
        f"unsupported={counts['unsupported']}",
    ]
    lines.extend(_render_entry(entry) for entry in payload["sessions"])
    last = payload["last"]
    lines.append(f"last: {_render_entry(last).strip()}" if last else "last: none")
    return "\n".join(lines)


def _status_json() -> str:
    return json.dumps(_status_payload(), ensure_ascii=False)


# ── probe ───────────────────────────────────────────────────────────────────

def _probe(text: str) -> str:
    """One bounded classification of the operator's own text — nothing stored."""
    result = _middleware.run_probe(text)
    return json.dumps({
        "schema": PROBE_SCHEMA,
        "text_chars": len(text),
        "score": result["score"],
        "label": result["label"],
        "failure": result["failure"],
        "elapsed_ms": result["elapsed_ms"],
        "at": time.time(),
    }, ensure_ascii=False)
