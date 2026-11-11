"""Live probe of the OpenCode Go reasoning-effort vocabularies (measurement only).

Why this exists
---------------
The plugin registers an exact per-model wire vocabulary for the OpenCode Go routes
(:data:`effort.OPEN_CODE_GO_INJECTION_ROUTES`). Those lists were taken from the
vendor catalog. A catalog is not evidence: this script fires one real request per
``(model, level)`` and records whether the transport accepted the value, so the
tables can be rebuilt from measurements instead of a published list.

Transport (resolved on this machine, 2026-11-11; no secret is ever printed)
--------------------------------------------------------------------------
* base URL ``https://opencode.ai/zen/go/v1``, provider ``opencode-go`` in the
  OpenCode CLI model catalog (the provider is OpenAI-compatible:
  ``@ai-sdk/openai-compatible`` by default, ``@ai-sdk/openai`` for the per-model
  Responses routes).
* credential: the OpenCode CLI store (``<opencode config>/auth.json``, entry
  ``opencode-go``, field ``key``), else ``OPENCODE_GO_API_KEY`` / ``OPENCODE_API_KEY``.
  The value is read at call time and only sent as ``Authorization: Bearer``.

Shape of a probe
----------------
One POST, ``max_tokens: 8`` (Chat Completions) or ``max_output_tokens: 16``
(Responses, whose floor is 16), ``temperature: 0``, fixed prompt, no streaming, 30 s
timeout, no redirect followed. The effort goes on the carrier that
:data:`effort.OPEN_CODE_GO_INJECTION_ROUTES` names for the route's API mode:

* ``reasoning_effort`` — top-level ``reasoning_effort`` on ``/chat/completions``;
* ``paired_effort`` — top-level ``thinking: {"type": "enabled"}`` plus
  ``reasoning_effort`` (``extra_body`` is flattened on the wire);
* ``reasoning`` — top-level ``reasoning: {"effort": ...}`` on ``/responses``.

A model without a registry entry is probed with ``reasoning_effort`` on Chat
Completions, as the task brief requires.

Usage::

    python scripts/probe_opencode_go.py --dry-run
    python scripts/probe_opencode_go.py
    python scripts/probe_opencode_go.py --models grok-4.7 --levels low,high

Each probe appends one redacted JSON line to
``docs/validation/runs/opencode-go-20261111/probe.jsonl``; the prompt, the key and
the response body are never written (at most 200 characters of an error ``message``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import effort  # noqa: E402  (payload module: pure stdlib, no Hermes import at top level)

#: OpenCode Go base URL (provider ``opencode-go``, ``api`` in the CLI catalog).
DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
#: Fixed probe prompt: short, deterministic, no tool use.
PROBE_PROMPT = "Write the single word: ok"
DEFAULT_TIMEOUT_S = 30.0
#: Written relative to the repository root.
DEFAULT_OUT = REPO_ROOT / "docs" / "validation" / "runs" / "opencode-go-20261111" / "probe.jsonl"
#: Statuses that mean the route refused this level (stop the route there).
HARD_REJECTIONS = frozenset({400, 422})
MODEL_MISSING = frozenset({404})
AUTH_FAILURES = frozenset({401, 403})
CHAT_COMPLETIONS = "chat_completions"
CODEX_RESPONSES = "codex_responses"
CARRIER_REASONING_EFFORT = "reasoning_effort"
CARRIER_PAIRED = "paired_effort"
CARRIER_REASONING = "reasoning"
DEFAULT_CARRIER = CARRIER_REASONING_EFFORT

#: The planned matrix, in probe order. Levels are tried top to bottom; the first
#: hard rejection ends that route.
MATRIX = (
    ("minimax-m3.1-flash", ("low", "medium", "high", "xhigh", "max")),
    ("step-5-preview", ("low", "medium", "high")),
    ("glm-5.3-flash", ("low", "high", "max")),
    ("beam-free", ("low", "medium", "high", "xhigh", "max")),
    ("deepseek-v4.1-flash", ("low", "medium", "high", "max")),
    ("grok-4.7", ("low", "medium", "high", "xhigh")),
    ("deepseek-v4-pro", ("low", "high", "max")),
    ("muse-spark-1.3-contributor", ("minimal", "low", "medium", "high", "xhigh")),
)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every 3xx into an ``HTTPError`` instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(NoRedirectHandler())
    return _OPENER


def _auth_store_paths():
    """Candidate OpenCode CLI credential stores, in resolution order."""
    home = Path.home()
    candidates = []
    if os.environ.get("OPENCODE_AUTH_PATH"):
        candidates.append(Path(os.environ["OPENCODE_AUTH_PATH"]))
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        candidates.append(Path(config_home) / "opencode" / "auth.json")
    candidates.append(home / ".config" / "opencode" / "auth.json")
    candidates.append(home / ".local" / "share" / "opencode" / "auth.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "opencode" / "auth.json")
    return candidates


def resolve_key():
    """The OpenCode Go key, read at call time. Never logged, never returned to a caller
    that could print it."""
    for name in ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, "env:" + name
    for path in _auth_store_paths():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            store = json.loads(raw)
        except ValueError:
            continue
        entry = store.get("opencode-go")
        if isinstance(entry, dict):
            key = entry.get("key")
            if isinstance(key, str) and key.strip():
                return key.strip(), "auth.json:opencode-go.key"
    return "", "missing"


def route_for(model):
    """``(api_mode, carrier)`` for *model*, from the plugin's own registry.

    A model with no registry entry is probed on Chat Completions with
    ``reasoning_effort`` (reported as an assumption, not as evidence).
    """
    for api_mode in (CHAT_COMPLETIONS, CODEX_RESPONSES):
        entry = effort.OPEN_CODE_GO_INJECTION_ROUTES.get(api_mode, {}).get(model)
        if entry:
            return api_mode, entry[0]
    return CHAT_COMPLETIONS, DEFAULT_CARRIER


def build_body(model, api_mode, carrier, level):
    """The request body: the fixed probe, with the effort on the existing carrier only."""
    if api_mode == CODEX_RESPONSES:
        return {
            "model": model,
            "input": PROBE_PROMPT,
            "reasoning": {"effort": level},
            # The Responses routes (Muse) reject anything below 16 with HTTP 400
            # ``max_output_tokens must be >= 16``, which is a probe defect, not a
            # level rejection.
            "max_output_tokens": 16,
            "temperature": 0,
            "stream": False,
        }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    if carrier == CARRIER_PAIRED:
        body["thinking"] = {"type": "enabled"}
    body["reasoning_effort"] = level
    return body


def endpoint_for(base_url, api_mode):
    base = base_url.rstrip("/")
    if api_mode == CODEX_RESPONSES:
        return base + "/responses"
    return base + "/chat/completions"


def _scrub(text, key):
    """Remove the credential from a diagnostic string before it reaches the log."""
    if not text:
        return ""
    if key:
        text = text.replace(key, "<redacted>")
    return text[:200]


def _error_code(payload):
    """``error.code`` when the server sends one, else a bounded ``error.message``."""
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, str):
        return err[:200]
    if not isinstance(err, dict):
        return None
    code = err.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()[:200]
    message = err.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()[:200]
    return None


def _read_error(response):
    try:
        raw = response.read(65536)
    except Exception:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None


def probe(base_url, key, session_id, model, api_mode, carrier, level, timeout_s):
    """Send one probe. Returns the record dict plus a transport-level ``stop`` flag."""
    url = endpoint_for(base_url, api_mode)
    body = build_body(model, api_mode, carrier, level)
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "hermes-adaptive-effort-probe/1")
    # The Go gateway answers 400 without this routing header (documented at
    # https://opencode.ai/v2/docs/console/go/#where-can-i-use-it); it is an opaque
    # session id, not a credential.
    request.add_header("x-opencode-session", session_id)
    if key:
        request.add_header("Authorization", "Bearer " + key)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "api_mode": api_mode,
        "level": level,
        "carrier": carrier,
        "status": 0,
        "rejected": False,
        "error_code": None,
        "latency_ms": 0.0,
        "redirected": False,
    }
    stop_all = False
    started = time.monotonic()
    try:
        response = _opener().open(request, timeout=timeout_s)
        try:
            response.read(65536)
            record["status"] = int(response.status)
        finally:
            response.close()
    except urllib.error.HTTPError as error:
        record["status"] = int(error.code)
        record["redirected"] = 300 <= int(error.code) < 400
        payload = _read_error(error)
        error.close()
        code = _error_code(payload)
        record["error_code"] = _scrub(code, key) if code else None
        if int(error.code) in HARD_REJECTIONS:
            record["rejected"] = True
        if int(error.code) in AUTH_FAILURES:
            stop_all = True
    except Exception as error:  # transport failure: no status at all
        record["status"] = 0
        record["error_code"] = type(error).__name__
    record["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    return record, stop_all


def _print_record(record):
    verdict = "accepted" if record["status"] == 200 else (
        "rejected" if record["rejected"] else "error")
    line = "{:<28} {:<9} {:<16} {:>4} {:<9}".format(
        record["model"], record["level"], record["carrier"], record["status"], verdict)
    if record["error_code"]:
        line += " " + record["error_code"][:120]
    print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Probe which reasoning-effort levels the OpenCode Go routes accept.")
    parser.add_argument("--models", default="",
                        help="comma-separated model filter (default: the whole matrix)")
    parser.add_argument("--levels", default="",
                        help="comma-separated level filter (default: each route's plan)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the planned matrix and send nothing")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="OpenCode Go API base (default: %(default)s)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="JSONL output path (default: docs/validation/runs/...)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="per-request timeout in seconds (default: %(default)s)")
    parser.add_argument("--session-id", default="",
                        help="value for the required x-opencode-session header "
                             "(default: a fresh UUID for the run)")
    args = parser.parse_args(argv)

    wanted_models = {m.strip() for m in args.models.split(",") if m.strip()}
    wanted_levels = {lv.strip() for lv in args.levels.split(",") if lv.strip()}

    planned = []
    for model, levels in MATRIX:
        if wanted_models and model not in wanted_models:
            continue
        api_mode, carrier = route_for(model)
        chosen = [lv for lv in levels if not wanted_levels or lv in wanted_levels]
        if chosen:
            planned.append((model, api_mode, carrier, chosen, levels))

    if args.dry_run:
        print("dry run: {0} route(s), {1} request(s), nothing sent".format(
            len(planned), sum(len(item[3]) for item in planned)))
        print("endpoint base: " + args.base_url)
        print("output: " + args.out)
        print("")
        print("{:<28} {:<16} {:<16} {}".format("model", "api_mode", "carrier", "levels"))
        for model, api_mode, carrier, chosen, declared in planned:
            note = "" if chosen == list(declared) else "  (planned)"
            print("{:<28} {:<16} {:<16} {}{}".format(
                model, api_mode, carrier, ",".join(chosen), note))
        return 0

    key, key_source = resolve_key()
    if not key:
        print("STOP: no OpenCode Go credential found (env OPENCODE_GO_API_KEY, "
              "OPENCODE_API_KEY, or the CLI auth store). Nothing sent.", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    session_id = args.session_id.strip() or str(uuid.uuid4())
    print("credential source: " + key_source)
    print("endpoint base: " + args.base_url)
    print("output: " + str(out_path))
    print("")
    print("{:<28} {:<9} {:<16} {:>4} {:<9} {}".format(
        "model", "level", "carrier", "HTTP", "verdict", "error"))

    records = []
    stop_all = False
    for model, api_mode, carrier, chosen, _declared in planned:
        if stop_all:
            break
        for level in chosen:
            record, stop_all = probe(args.base_url, key, session_id, model, api_mode,
                                     carrier, level, args.timeout)
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
            records.append(record)
            _print_record(record)
            if stop_all:
                print("STOP: authentication failed (HTTP {0}); not retrying with another "
                      "credential source.".format(record["status"]), file=sys.stderr)
                break
            if record["rejected"] or record["status"] in MODEL_MISSING:
                break
    accepted = sum(1 for r in records if r["status"] == 200)
    print("")
    print("{0} probe(s): {1} accepted, {2} rejected, {3} error/other".format(
        len(records), accepted,
        sum(1 for r in records if r["rejected"]),
        sum(1 for r in records if r["status"] != 200 and not r["rejected"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
