"""Jev-Auto — opt-in per-request reasoning-effort router.

Registered capabilities: ``llm_request`` middleware + ``on_session_end`` hook.
Everything else (effort mapping, the Jev adapter, session state) lives in
sibling modules so it can be unit-tested without booting Hermes.
"""

from __future__ import annotations

from typing import Any

from . import middleware as _middleware


def register(ctx: Any) -> None:
    """Attach the middleware and the session cleanup hook to a real PluginContext."""
    # Read settings through the context facade when it offers one, so a config
    # edit is picked up per call rather than frozen at import time.
    if hasattr(ctx, "get_config"):
        _middleware._settings_provider = ctx.get_config

    ctx.register_middleware("llm_request", _middleware.on_llm_request)
    # Verified hook name: `on_session_end(session_id=...)` clears in-memory state.
    ctx.register_hook("on_session_end", _middleware.on_session_end)
