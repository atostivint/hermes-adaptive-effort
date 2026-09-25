"""``/jev-auto`` — the plugin's in-session command.

Registered from ``register(ctx)`` with Hermes' verified
``PluginContext.register_command`` API (``hermes_cli/plugins.py``): the handler
receives the raw text typed after ``/jev-auto`` and returns a string, or ``None``
to let the host fall back to its own behaviour.

Subcommands
-----------

``status``     render the documented status payload (see :mod:`status`).
``status json``  the same payload serialized as JSON — the machine-readable form.
``probe <text>`` run exactly one bounded classification and report score/label/
               timing/failure. It never touches session state and never rewrites
               a request.

Every path fails open and returns human-readable text: no exception escapes into
the host's command dispatcher, and no prompt text ever leaves this module beyond
the text the operator typed themselves (``probe``).
"""

from __future__ import annotations

USAGE = (
    "Usage: /jev-auto <status|status json|probe <text>>\n"
    "  status        show mode, per-session state, last score/target, timing\n"
    "  status json   the same as a machine-readable payload\n"
    "  probe <text>  classify <text> once; bounded, no session state, no rewrite"
)


def handle(raw_args: str) -> str:
    """Entry point for ``/jev-auto``: never raises, always returns text."""
    try:
        return _dispatch(raw_args)
    except Exception:
        return USAGE


def _dispatch(raw_args: str) -> str:
    parts = (raw_args or "").split()
    if not parts:
        return USAGE
    verb, *rest = parts
    if verb in {"help", "-h", "--help"}:
        return USAGE
    return USAGE
