"""Jev-Auto Effort — opt-in per-request reasoning-effort router.

Registered capabilities: ``llm_request`` middleware + ``on_session_end`` hook.
Everything else (effort mapping, the Jev adapter, session state) lives in
sibling modules so it can be unit-tested without booting Hermes.
"""

from __future__ import annotations

from typing import Any

from . import command as _command
from . import middleware as _middleware


def register(ctx: Any) -> None:
    """Attach the middleware, the session cleanup hook and ``/jev-auto-effort`` to a real PluginContext."""
    # Read settings through the context facade when it offers one, so a config
    # edit is picked up per call rather than frozen at import time.
    if hasattr(ctx, "get_config"):
        _middleware._settings_provider = ctx.get_config

    ctx.register_middleware("llm_request", _middleware.on_llm_request)
    # Verified hook name: `on_session_end(session_id=...)` clears in-memory state.
    ctx.register_hook("on_session_end", _middleware.on_session_end)
    # Verified hook names (tools/delegate_tool.py emits them with these kwargs):
    #   subagent_start(parent_session_id=…, child_session_id=…, child_goal=…)
    #   subagent_stop(parent_session_id=…, child_session_id=…)
    # Together they let a child's own request be recognised and classified from
    # the goal its parent wrote.
    ctx.register_hook("subagent_start", _middleware.on_subagent_start)
    ctx.register_hook("subagent_stop", _middleware.on_subagent_stop)
    # Verified API: PluginContext.register_command (hermes_cli/plugins.py) -> `/jev-auto-effort`.
    ctx.register_command(
        "jev-auto-effort",
        handler=_command.handle,
        description=(
            "Jev-Auto Effort reasoning-effort router: status, bounded probe, and "
            "off|recommend|auto|cache_safe for future requests"
        ),
        args_hint="<status|status json|probe <text>|off|recommend|auto|cache_safe>",
    )
