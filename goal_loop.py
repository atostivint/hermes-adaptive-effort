"""Local markers for Hermes' goal-continuation loops.

The host's ``/goal`` loop sends its next synthetic turn as a normal user message whose first
line is a fixed marker (``hermes_cli/goals.py``). That continuation means the previous turn did
not satisfy the goal judge, or a quality gate failed. The kanban goal loop uses a second marker
for "judge says it is not done yet". Both are external, verified-failure signals for a reasoning
effort ladder; the finalize nudge ("the work looks complete") is not a failure and is deliberately
not matched.

This module is pure and stdlib-only. It only compares the start of the latest user text; it
never stores, logs or sends that text.
"""

from __future__ import annotations

from typing import Any, Tuple

#: Host-generated first line of a ``/goal`` continuation (all variants share this prefix).
GOAL_CONTINUATION_PREFIX = "[Continuing toward your standing goal"
#: Host-generated first line of a kanban goal continuation.
KANBAN_CONTINUATION_PREFIX = "[Continuing toward this kanban task"

PREFIXES: Tuple[str, ...] = (GOAL_CONTINUATION_PREFIX, KANBAN_CONTINUATION_PREFIX)


def is_goal_continuation(text: Any) -> bool:
    """True when *text* starts with a host-generated goal-continuation marker."""
    if not isinstance(text, str):
        return False
    return text.lstrip().startswith(PREFIXES)
