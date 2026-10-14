"""One bounded retry for a transient scorer HTTP status (stdlib only, no I/O of its own).

A scorer that answers 408/429/500/502/503/504 gave no answer at all, so the call may be
repeated once, but only inside the caller's existing deadline: the retry's own timeout is
the remaining budget, so the total never exceeds ``timeout_s``. Timeouts, connection
errors, every other status and every answer that was received (malformed, refused,
out-of-list) are never retried: they surface to the adapter exactly as before.
"""

from __future__ import annotations

import email.utils
import time
import urllib.error
from typing import Any, Callable, Optional

#: HTTP statuses that mean "no answer yet, try again".
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
#: A retry needs at least this much of the deadline left after the wait.
MIN_REMAINING_S = 0.3
#: Wait before the retry when the server sent no usable ``Retry-After``.
DEFAULT_WAIT_S = 0.1


def _status_of(obj: Any) -> Optional[int]:
    status = getattr(obj, "status", None)
    if not isinstance(status, int):
        status = getattr(obj, "code", None)
    return status if isinstance(status, int) else None


def retry_after_s(obj: Any, wall_clock: Optional[Callable[[], float]] = None) -> Optional[float]:
    """Seconds named by ``Retry-After`` (delay-seconds or HTTP-date), else ``None``."""
    headers = getattr(obj, "headers", None)
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except Exception:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isascii() and text.isdigit():
        return float(text)
    try:
        moment = email.utils.parsedate_to_datetime(text)
        return max(0.0, moment.timestamp() - (wall_clock or time.time)())
    except Exception:
        return None


def _close(obj: Any) -> None:
    try:
        obj.close()
    except Exception:
        pass


def send_with_retry(send: Callable[[float], Any], deadline_s: float, *,
                    clock: Optional[Callable[[], float]] = None,
                    sleep: Optional[Callable[[float], Any]] = None,
                    wall_clock: Optional[Callable[[], float]] = None) -> Any:
    """Run ``send(timeout_s)``; retry once on a transient status if the deadline allows.

    Returns the response object or raises, exactly as ``send`` does. When the retry is
    declined, the first attempt's response or exception is what the caller sees; when
    the retry runs, the second attempt's is.
    """
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    wall_clock = wall_clock or time.time
    started = clock()
    first_exc: Optional[BaseException] = None
    first: Any = None
    try:
        first = send(deadline_s)
    except Exception as exc:
        if not isinstance(exc, urllib.error.HTTPError) or _status_of(exc) not in TRANSIENT_STATUSES:
            raise
        first_exc = exc
    carrier = first_exc if first_exc is not None else first
    if first_exc is None and _status_of(first) not in TRANSIENT_STATUSES:
        return first

    wait = retry_after_s(carrier, wall_clock)
    if wait is None:
        wait = DEFAULT_WAIT_S
    remaining = deadline_s - (clock() - started)
    if remaining - wait < MIN_REMAINING_S:
        if first_exc is not None:
            raise first_exc
        return first
    _close(carrier)
    sleep(wait)
    remaining = deadline_s - (clock() - started)
    if remaining < MIN_REMAINING_S:
        # The sleep overran; the first failure stands and no second request is made.
        if first_exc is not None:
            raise first_exc
        return first
    return send(remaining)
