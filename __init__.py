"""Hermes Adaptive Effort — opt-in per-request reasoning-effort router.

Registered capabilities: ``llm_request`` middleware + session lifecycle hooks.
Everything else (effort mapping, scorer adapters, session state) lives in
sibling modules so it can be unit-tested without booting Hermes.
"""

from __future__ import annotations

from typing import Any

from . import command as _command
from . import middleware as _middleware


def register(ctx: Any) -> None:
    """Attach middleware, lifecycle hooks and ``/hermes-adaptive-effort`` to a real PluginContext."""
    # Read settings through the context facade when it offers one, so a config
    # edit is picked up per call rather than frozen at import time.
    if hasattr(ctx, "get_config"):
        _middleware._settings_provider = ctx.get_config

    # Newer Hermes hosts expose a thread-safe TUI status item. Older hosts keep
    # working with the CLI log and /status command until that API is available.
    register_cli_status_item = getattr(ctx, "register_cli_status_item", None)
    if callable(register_cli_status_item):
        try:
            # "N/A", not an em dash: this renders in a terminal status bar, and on a
            # Windows console a non-ASCII glyph degrades to a replacement box. It also
            # states the truth — a route with no writable effort field has no effort
            # to report, as opposed to one that simply has not decided yet.
            status_handle = register_cli_status_item("effort", "Effort: N/A", priority=30)
            _middleware.set_cli_status_handle(status_handle)
        except Exception:
            _middleware.set_cli_status_handle(None)

    ctx.register_middleware("llm_request", _middleware.on_llm_request)
    # Hermes fires on_session_end after each turn with turn_id; preserve the bounded
    # per-turn status until the actual on_session_finalize / on_session_reset boundary.
    ctx.register_hook("on_session_end", _middleware.on_session_end)
    ctx.register_hook("on_session_finalize", _middleware.on_session_finalize)
    ctx.register_hook("on_session_reset", _middleware.on_session_reset)
    # Verified hook names (tools/delegate_tool.py emits them with these kwargs):
    #   subagent_start(parent_session_id=…, child_session_id=…, child_goal=…)
    #   subagent_stop(parent_session_id=…, child_session_id=…)
    # Together they let a child's own request be recognised and classified from
    # the goal its parent wrote.
    ctx.register_hook("subagent_start", _middleware.on_subagent_start)
    ctx.register_hook("subagent_stop", _middleware.on_subagent_stop)
    # Verified API: PluginContext.register_command (hermes_cli/plugins.py) -> `/hermes-adaptive-effort`.
    ctx.register_command(
        "hermes-adaptive-effort",
        handler=_command.handle,
        description=(
            "Hermes Adaptive Effort reasoning-effort router: status, bounded probe, and "
            "off|recommend|auto|cache_safe|inject for future requests"
        ),
        args_hint="<status|status json|probe <text>|off|recommend|auto|cache_safe|inject>",
    )
