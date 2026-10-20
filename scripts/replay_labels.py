"""Pure cheapest-passing-level logic for the effort replay (stdlib only, no I/O).

An *outcome* is one of ``pass``, ``fail`` or ``rejected``. ``rejected`` means the provider
refused the effort value itself (HTTP 400/422); it is not a failed check and never counts
against a level's pass rate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

PASS, FAIL, REJECTED = "pass", "fail", "rejected"
_EPSILON = 1e-9


def pass_rate(outcomes: Sequence[str], n: int) -> Optional[float]:
    """passes / n over *n* planned samples; ``None`` when the level was rejected."""
    if n <= 0 or (outcomes and all(o == REJECTED for o in outcomes)):
        return None
    return round(sum(1 for o in outcomes if o == PASS) / n, 4)


def derive_label(levels: Sequence[str], outcomes: Dict[str, Sequence[str]], n: int,
                 threshold: float = 1.0) -> Dict[str, Any]:
    """Cheapest level whose pass rate reaches *threshold*, plus the noise indicators.

    ``levels`` is ordered cheapest first. ``dropped`` means no accepted level passes (no label).
    ``non_monotonic`` means a lower accepted level passes while a higher accepted one does not.
    ``floor`` means the label is the lowest accepted level (nothing cheaper was available).
    """
    rates = {level: pass_rate(outcomes.get(level, ()), n) for level in levels}
    accepted: List[str] = [level for level in levels if rates[level] is not None]
    passing = [level for level in accepted if rates[level] + _EPSILON >= threshold
               and rates[level] > 0]
    label = passing[0] if passing else None
    non_monotonic = False
    if passing:
        first = accepted.index(passing[0])
        non_monotonic = any(level not in passing for level in accepted[first + 1:])
    return {
        "pass_rate": rates,
        "label": label,
        "dropped": label is None,
        "non_monotonic": non_monotonic,
        "floor": bool(label) and label == (accepted[0] if accepted else None),
        "rejected_levels": [level for level in levels if rates[level] is None
                            and outcomes.get(level)],
    }
