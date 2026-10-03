"""Hermes Adaptive Effort dashboard/desktop backend, mounted at ``/api/plugins/hermes-adaptive-effort/``.

Thin wrapper around the agent half's :mod:`command` / :mod:`middleware`: the same
``hermes-adaptive-effort.status.v1`` payload ``/hermes-adaptive-effort status json`` prints, a
runtime mode switch with the same semantics (future requests of this process, never a
config write unless ``persist`` is set), and ``GET /changes`` — the bounded feed of
rewrites that actually reached a request, so the desktop chip can say which reasoning
effort is now in force. Fail-open by contract: any error is a 5xx /
``failure`` field, never a broken turn, and no prompt text is ever stored or echoed.

Module-resolution note: the dashboard loader imports this file as a top-level module
(``hermes_dashboard_plugin_hermes-adaptive-effort``), NOT as part of the agent package, so a
plain relative import would load a SECOND copy of the middleware with its own empty
``_SESSIONS`` / ``_MODE_OVERRIDE``. :func:`_agent_modules` therefore prefers the
already-loaded ``hermes_plugins.hermes_adaptive_effort.*`` modules (same process under
``hermes serve`` → shared live state) and only falls back to loading the files from
disk (degraded: fresh state, still correct shapes).
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PLUGIN_ID = "hermes-adaptive-effort"
STATUS_SCHEMA = "hermes-adaptive-effort.status.v1"
PROBE_SCHEMA = "hermes-adaptive-effort.probe.v1"
CHANGES_SCHEMA = "hermes-adaptive-effort.changes.v1"
VALID_MODES = ("off", "recommend", "auto", "cache_safe")
MAX_PROBE_CHARS = 4000

try:  # FastAPI exists in the serve/gateway env, not in the plugin's unit venv.
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field

    _HAS_HTTP = True
except Exception:  # pragma: no cover - unit env without FastAPI
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    BaseModel = object  # type: ignore[assignment,misc]

    def Field(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return None

    _HAS_HTTP = False

router = APIRouter() if _HAS_HTTP else None

_AGENT_PKG = "hermes_plugins.hermes_adaptive_effort"
_TEST_PKG = "hermes_plugin_adaptive_effort"
_FALLBACK_PKG = "hermes_dashboard_hermes_adaptive_effort_pkg"

#: Pinned ``(middleware, command)`` pair. ``None`` in production (resolve from the
#: live process). Tests pin the hermetic copies so a neighbour suite's real
#: PluginManager load (which stays in ``sys.modules`` by design) cannot leak its
#: config/factory/state into these assertions.
_PINNED: Optional[Tuple[Any, Any]] = None


def _plugin_root() -> Path:
    """Agent payload dir: ``.../hermes-adaptive-effort/`` (parent of ``dashboard/``)."""
    return Path(__file__).resolve().parents[1]


def _loaded_agent_modules() -> Tuple[Any, Any]:
    """Already-imported agent modules sharing live middleware state, if any."""
    for prefix in (_AGENT_PKG, _TEST_PKG):
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "__home_"):
                middleware = sys.modules.get(name + ".middleware")
                command = sys.modules.get(name + ".command")
                if middleware is not None and command is not None:
                    return middleware, command
        for name in list(sys.modules):
            if name.startswith(prefix) and name.endswith(".middleware"):
                base = name[: -len(".middleware")]
                middleware = sys.modules.get(name)
                command = sys.modules.get(base + ".command")
                if middleware is not None and command is not None:
                    return middleware, command
    return None, None


def _load_from_disk() -> Tuple[Any, Any]:
    """Import the agent files fresh from this install (degraded: no shared state)."""
    root = _plugin_root()
    pkg_name = _FALLBACK_PKG
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        pkg = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(pkg_name, None, is_package=True))
        pkg.__path__ = [str(root)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg

    def _load(stem: str) -> Any:
        full = f"{pkg_name}.{stem}"
        if full in sys.modules:
            return sys.modules[full]
        path = root / f"{stem}.py"
        spec = importlib.util.spec_from_file_location(full, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot build spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        return module

    return _load("middleware"), _load("command")


def _agent_modules() -> Tuple[Any, Any]:
    """``(middleware, command)`` — live shared modules preferred, disk fallback."""
    if _PINNED is not None:
        return _PINNED
    middleware, command = _loaded_agent_modules()
    if middleware is not None and command is not None:
        return middleware, command
    try:
        return _load_from_disk()
    except Exception:
        logger.debug("hermes-adaptive-effort: agent modules unavailable; degrading", exc_info=True)
        return None, None


def get_status_payload() -> Dict[str, Any]:
    """Live ``hermes-adaptive-effort.status.v1`` payload (same builder as the slash command)."""
    middleware, command = _agent_modules()
    if command is None or middleware is None:
        return {"schema": STATUS_SCHEMA, "plugin": PLUGIN_ID,
                "error": "agent_plugin_not_loaded"}
    try:
        payload = command._status_payload()
    except Exception:
        logger.debug("hermes-adaptive-effort: status build failed", exc_info=True)
        return {"schema": STATUS_SCHEMA, "plugin": PLUGIN_ID, "error": "status_failed"}
    if not isinstance(payload, dict):
        return {"schema": STATUS_SCHEMA, "plugin": PLUGIN_ID, "error": "status_failed"}
    payload.setdefault("schema", STATUS_SCHEMA)
    payload.setdefault("plugin", PLUGIN_ID)
    return payload


def get_changes_payload() -> Dict[str, Any]:
    """Recent applied effort changes for Desktop notifications and status."""
    middleware, _ = _agent_modules()
    if middleware is None:
        return {"schema": CHANGES_SCHEMA, "plugin": PLUGIN_ID,
                "error": "agent_plugin_not_loaded"}
    try:
        state = middleware.effort_change_state()
    except Exception:
        logger.debug("hermes-adaptive-effort: change feed build failed", exc_info=True)
        return {"schema": CHANGES_SCHEMA, "plugin": PLUGIN_ID,
                "error": "changes_failed"}
    return {
        "schema": CHANGES_SCHEMA,
        "plugin": PLUGIN_ID,
        "stream_id": state["stream_id"],
        "events": state["events"],
        "latest": state["latest"],
    }


def set_mode(mode: Any, *, persist: bool = False) -> Dict[str, Any]:
    """Runtime mode switch for future requests of this process (never echoes config).

    ``persist=True`` additionally writes ``plugins.entries.hermes-adaptive-effort.settings.mode``
    through the canonical writer, so the choice survives a restart — the same write the
    Desktop settings form performs. Default is runtime-only, mirroring
    ``/hermes-adaptive-effort <mode>``.
    """
    middleware, _ = _agent_modules()
    if middleware is None:
        return {"ok": False, "error": "agent_plugin_not_loaded", "mode": None}
    value = str(mode or "").strip().lower()
    if value not in VALID_MODES:
        return {"ok": False, "error": f"unknown mode {value!r}; want one of {list(VALID_MODES)}",
                "mode": None}
    try:
        before = middleware._settings()["mode"]
    except Exception:
        before = None
    try:
        applied = middleware.set_mode_override(value)
    except Exception:
        logger.debug("hermes-adaptive-effort: set_mode_override failed", exc_info=True)
        return {"ok": False, "error": "mode_failed", "mode": None}
    if applied is None:
        return {"ok": False, "error": "mode_failed", "mode": None}
    persisted: Optional[bool] = None
    persist_error: Optional[str] = None
    if persist:
        try:
            from hermes_cli.plugins_state import _plugin_relative_segments, save_plugin_setting
            save_plugin_setting(PLUGIN_ID, _plugin_relative_segments("mode"), applied)
            persisted = True
        except Exception:
            persisted = False
            # Exception text can contain config paths, prompt text or secrets.
            # Keep the public API response bounded to a stable failure code.
            persist_error = "persist_failed"
    out: Dict[str, Any] = {"ok": True, "before": before, "mode": applied,
                           "scope": "future requests in this process"}
    if persisted is not None:
        out["persisted"] = persisted
        if persist_error:
            out["persist_error"] = persist_error
    return out


def run_probe(text: Any) -> Dict[str, Any]:
    """One bounded classification of operator-typed text; stores nothing, echoes nothing."""
    middleware, _ = _agent_modules()
    if middleware is None:
        return {"schema": PROBE_SCHEMA, "text_chars": 0, "score": None,
                "label": None, "failure": "agent_plugin_not_loaded",
                "elapsed_ms": 0.0, "at": time.time()}
    clipped = text if isinstance(text, str) else ""
    if len(clipped) > MAX_PROBE_CHARS:
        clipped = clipped[:MAX_PROBE_CHARS]
    try:
        result = middleware.run_probe(clipped)
    except Exception:
        logger.debug("hermes-adaptive-effort: probe failed", exc_info=True)
        return {"schema": PROBE_SCHEMA, "text_chars": len(clipped), "score": None,
                "label": None, "failure": "probe_failed", "elapsed_ms": 0.0,
                "at": time.time()}
    return {"schema": PROBE_SCHEMA, "text_chars": len(clipped),
            "score": result.get("score"), "label": result.get("label"),
            "failure": result.get("failure"), "elapsed_ms": result.get("elapsed_ms", 0.0),
            "at": time.time()}


if _HAS_HTTP and router is not None:  # pragma: no cover - needs serve env

    class ModeBody(BaseModel):
        mode: str = Field(description="off|recommend|auto|cache_safe")
        persist: bool = Field(default=False, description="also write settings.mode")

    class ProbeBody(BaseModel):
        text: str = Field(description="operator-typed text to classify once")

    @router.get("/status")
    def status() -> Dict[str, Any]:
        payload = get_status_payload()
        if payload.get("error"):
            raise HTTPException(status_code=503, detail=payload["error"])
        return payload

    @router.get("/changes")
    def changes() -> Dict[str, Any]:
        payload = get_changes_payload()
        if payload.get("error"):
            raise HTTPException(status_code=503, detail=payload["error"])
        return payload

    @router.post("/mode")
    def switch_mode(body: ModeBody) -> Dict[str, Any]:
        result = set_mode(body.mode, persist=bool(body.persist))
        if not result.get("ok"):
            raise HTTPException(status_code=400 if "unknown mode" in str(result.get("error")) else 503,
                                detail=result.get("error"))
        if result.get("persisted") is False:
            raise HTTPException(status_code=500, detail=result.get("persist_error") or "persist_failed")
        return result

    @router.post("/probe")
    def probe(body: ProbeBody) -> Dict[str, Any]:
        if not isinstance(body.text, str) or not body.text.strip():
            raise HTTPException(status_code=400, detail="text is required")
        return run_probe(body.text)

