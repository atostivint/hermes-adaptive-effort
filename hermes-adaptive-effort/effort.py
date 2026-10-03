"""Pure rubric-score -> effort mapping.

No I/O, no Hermes imports at module import time (core is imported lazily so the
unit tests run without a Hermes install). Rules:

* Scorers answer an ordered 3-level score rubric: 0 = low, 1 = medium, 2 = high,
  with deterministic thresholds <0.5, 0.5..<1.5, >=1.5.
* Anything outside 0..2, non-finite, or non-numeric is invalid -> ``None``.
* The target level is always clamped onto the route's own declared vocabulary
  (``agent.reasoning_effort.clamp_effort``): never an unsupported wire value.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

#: Normalized labels Hermes Adaptive Effort may select. Never a fourth value.
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


def wire_efforts(provider: Optional[str], model: Optional[str]) -> tuple[str, ...]:
    """Wire vocabulary the (provider, model) route *really* accepts, when it is narrow.

    ``route_supported_efforts`` is Hermes' **entry** clamp: for every route that is
    not Codex it returns the widest OpenAI-compatible vocabulary, on the assumption
    that the transport clamps again downstream. A plugin hooked into
    ``llm_request`` runs **after** that transport clamp, so writing a level the wide
    set allows but the vendor rejects makes the request fail (Moonshot K3 and Ox
    Alpha reject ``medium``; GLM-5.2 rejects ``low`` and ``medium``). Returns ``()``
    when the route has no narrower declared set — the caller then falls back to
    Hermes' route data.
    """
    bare = (model or "").strip().lower().rsplit("/", 1)[-1]
    if not bare:
        return ()
    try:
        from agent import reasoning_effort as _core
    except Exception:
        return ()
    try:
        if "kimi" in bare or "moonshot" in bare or _core._KIMI_K3_SLUG_RE.search(bare):
            # K3 = low/high/max, K2-era = low/medium/high (host-side detection reused).
            return tuple(_core.kimi_supported_efforts(bare))
        for prefix, attribute in (("glm-5.2", "GLM52_EFFORTS"), ("glm-5.3", "GLM53_EFFORTS")):
            if bare.startswith(prefix):
                return tuple(getattr(_core, attribute))
    except Exception:
        return ()
    return ()


def wire_overrides(vocabulary: Sequence[str]) -> Optional[dict]:
    """Declared vendor re-mapping for a level the vendor spells differently.

    Kimi K3's positional middle is ``high`` (the server default), so ``medium``
    must round **up** to ``high`` rather than down to ``low``; without this the
    clamp would silently halve the requested effort.
    """
    try:
        from agent import reasoning_effort as _core
    except Exception:
        return None
    try:
        if tuple(vocabulary) == tuple(_core.KIMI_K3_EFFORTS):
            return dict(_core.KIMI_K3_OVERRIDES)
        if tuple(vocabulary) == tuple(_core.GLM52_EFFORTS):
            return dict(_core.GLM52_OVERRIDES)
        if tuple(vocabulary) == tuple(_core.GLM53_EFFORTS):
            return dict(_core.GLM53_OVERRIDES)
    except Exception:
        return None
    return None


def supported_efforts(provider: Optional[str], model: Optional[str],
                      supported: Optional[Sequence[str]] = None) -> tuple[str, ...]:
    """Levels the route accepts, from Hermes' own route vocabulary table.

    ``supported`` overrides the lookup (used by tests and by callers that already
    read the provider profile). A route with a narrower *wire* vocabulary than the
    OpenAI-compatible default wins over the default. Unknown/undeclared routes fall
    back to the widest OpenAI-compatible vocabulary — the same default
    ``clamp_effort`` applies.
    """
    if supported:
        return tuple(str(level) for level in supported)
    try:
        from agent.reasoning_effort import route_supported_efforts
    except Exception:
        return ()
    try:
        if (provider or "").strip().lower() != "openai-codex":
            narrow = wire_efforts(provider, model)
            if narrow:
                return narrow
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
        clamped = clamp_effort(level, vocabulary, wire_overrides(vocabulary))
    except Exception:
        return None
    if not isinstance(clamped, str) or clamped.strip().lower() not in vocabulary:
        return None
    return clamped.strip().lower()
