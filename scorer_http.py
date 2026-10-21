"""Shared HTTP plumbing for the scorer adapters (stdlib only, no Hermes import at top level).

Every adapter used to carry its own copy of the key reader, request builder,
exception-to-reason ladder and response parser. They live here once:

* :func:`default_transport` never follows a redirect, so an ``Authorization`` header is
  only ever sent to the configured host.
* :func:`send` maps every transport failure to the documented reason code and closes the
  response behind an ``HTTPError``; :func:`read_json` closes every other response.
* :func:`resolve_key` keeps a resolved API key for :data:`KEY_TTL_S` seconds. Only a
  non-empty key is cached, so adding a credential takes effect at once; rotating or
  removing one can lag by up to the TTL. ``credential_present`` never uses the cache and
  nothing here returns a secret to a caller other than the adapter that asked for it.

Retry semantics stay in :mod:`scorer_retry` and are not changed.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from . import scorer_retry

#: How long a resolved API key is reused before the secret scope / environment is read again.
KEY_TTL_S = 30.0

_KEY_CACHE: "dict[str, tuple[float, str]]" = {}
_KEY_LOCK = threading.Lock()


def read_secret(name: str) -> str:
    """Resolve *name* through Hermes' secret scope first, then the environment."""
    try:
        from agent.secret_scope import get_secret
        value = get_secret(name, "") or ""
        if str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return (os.environ.get(name) or "").strip()


def clear_key_cache() -> None:
    """Forget every cached key (tests, plugin reset)."""
    with _KEY_LOCK:
        _KEY_CACHE.clear()


def resolve_key(cache_id: str, reader: Callable[[], str], *,
                clock: Callable[[], float] = time.monotonic) -> str:
    """``reader()`` with a short TTL cache of a non-empty result under *cache_id*."""
    now = clock()
    with _KEY_LOCK:
        hit = _KEY_CACHE.get(cache_id)
        if hit is not None and now - hit[0] < KEY_TTL_S:
            return hit[1]
    try:
        value = str(reader() or "").strip()
    except Exception:
        return ""
    with _KEY_LOCK:
        if value:
            _KEY_CACHE[cache_id] = (now, value)
        else:
            _KEY_CACHE.pop(cache_id, None)
    return value


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every 3xx into an ``HTTPError`` instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER: Any = None


def _get_opener() -> Any:
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(NoRedirectHandler())
    return _OPENER


def default_transport(request: urllib.request.Request, timeout: float):
    """POST once, exactly as built, without following redirects."""
    return _get_opener().open(request, timeout=timeout)


def build_request(url: str, body: Any, *, key: str = "",
                  user_agent: str) -> urllib.request.Request:
    """JSON POST request; ``Authorization: Bearer`` only when *key* is non-empty."""
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": user_agent}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def send(request: urllib.request.Request, transport: Callable[..., Any],
         timeout: float) -> "tuple[Any, Optional[str]]":
    """``(payload, None)`` or ``(None, reason)``; never raises, always closes an error body."""
    try:
        return scorer_retry.send_with_retry(lambda t: transport(request, t), timeout), None
    except urllib.error.HTTPError as exc:
        close_response(exc)
        return None, "http_error"
    except urllib.error.URLError as exc:
        return None, ("timeout" if isinstance(getattr(exc, "reason", None), TimeoutError)
                      else "transport_error")
    except TimeoutError:
        return None, "timeout"
    except OSError:
        return None, "transport_error"
    except Exception:
        return None, "unexpected_error"


def read_json(payload: Any) -> "tuple[Any, Optional[str]]":
    """Parsed answer or a reason code; a non-2xx or unreadable response is closed."""
    status = getattr(payload, "status", None)
    if isinstance(status, int) and not 200 <= status < 300:
        close_response(payload)
        return None, "http_error"
    try:
        if hasattr(payload, "__enter__"):
            with payload as response:
                return (json.load(response) if hasattr(response, "read") else response), None
        if hasattr(payload, "read"):
            try:
                return json.load(payload), None
            finally:
                close_response(payload)
        return payload, None
    except Exception:
        close_response(payload)
        return None, "malformed_response"
