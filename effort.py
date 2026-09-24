"""Pure Jev-score -> effort mapping.

No I/O, no Hermes imports at module import time (core is imported lazily so the
unit tests run without a Hermes install). Rules:

* Jev answers an ordered 3-level score rubric: 0 = low, 1 = medium, 2 = high,
  with deterministic thresholds <0.5, 0.5..<1.5, >=1.5.
* Anything outside 0..2, non-finite, or non-numeric is invalid -> ``None``.
* The target level is always clamped onto the route's own declared vocabulary
  (``agent.reasoning_effort.clamp_effort``): never an unsupported wire value.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

#: Normalized labels Jev-Auto may select. Never a fourth value.
EFFORT_LABELS: tuple[str, ...] = ("low", "medium", "high")

#: Rubric boundaries (documented in README.md).
LOW_MAX = 0.5
MEDIUM_MAX = 1.5


def score_to_label(score) -> Optional[str]:
    """Rubric score -> normalized label; ``None`` for anything invalid."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    value = float(score)
    if not math.isfinite(value) or not 0.0 <= value <= 2.0:
        return None
    if value < LOW_MAX:
        return "low"
    if value < MEDIUM_MAX:
        return "medium"
    return "high"


def supported_efforts(provider: Optional[str], model: Optional[str],
                      supported: Optional[Sequence[str]] = None) -> tuple[str, ...]:
    """Levels the route accepts, from Hermes' own route vocabulary table.

    ``supported`` overrides the lookup (used by tests and by callers that already
    read the provider profile). Unknown/undeclared routes fall back to the widest
    OpenAI-compatible vocabulary — the same default ``clamp_effort`` applies.
    """
    if supported:
        return tuple(str(level) for level in supported)
    try:
        from agent.reasoning_effort import route_supported_efforts
    except Exception:
        return ()
    try:
        return tuple(route_supported_efforts(provider, model))
    except Exception:
        return ()


def map_effort(label: Optional[str], provider: Optional[str] = None,
               model: Optional[str] = None,
               supported: Optional[Sequence[str]] = None) -> Optional[str]:
    """Normalized label -> a wire-legal effort, or ``None`` when it cannot be mapped."""
    if not isinstance(label, str):
        return None
    level = label.strip().lower()
    if level not in EFFORT_LABELS:
        return None
    vocabulary = supported_efforts(provider, model, supported)
    if not vocabulary:
        return None
    try:
        from agent.reasoning_effort import clamp_effort
    except Exception:
        return None
    try:
        clamped = clamp_effort(level, vocabulary)
    except Exception:
        return None
    if not isinstance(clamped, str) or clamped.strip().lower() not in vocabulary:
        return None
    return clamped.strip().lower()
