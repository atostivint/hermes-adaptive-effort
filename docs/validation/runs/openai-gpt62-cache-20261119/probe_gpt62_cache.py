"""Live measurement of the GPT-6.2 prompt cache under reasoning-effort changes.

Why this exists
---------------
The plugin may change a request's reasoning effort between turns. OpenAI's GPT-6.2
family changes how prompt caching interacts with that control:

* top-level ``instructions`` are appended *after* ``input``, are billed uncached every
  request and cannot form a cache breakpoint; reusable instructions belong in a
  ``developer`` message at the start of ``input``;
* changing request-level ``reasoning.effort`` can rewrite hidden system instructions;
  the documented cache-preserving way is to keep top-level effort fixed and append a
  ``{"type":"configuration_update","reasoning":{"effort":"high"}}`` item to ``input``;
* responses may carry ``prompt_cache_diagnostics`` (e.g. ``reason: tools_changed``).

Four arms isolate those claims with an identical ~1,800-token synthetic prefix:

* A - control: effort ``low`` on every call (4 calls). Expect hits from call 2.
* B - top-level change: ``low, high, low, high, low`` (5 calls), same input every time.
* C - configuration_update: top-level effort stays ``low``; a ``configuration_update``
  item with ``high`` is appended each call from call 2 on, earlier items unchanged and
  in order (4 calls).
* D - placement: the filler moves to top-level ``instructions``, constant ``low``
  effort (4 calls); compare with A.

Transport (stdlib only, no secret ever printed or written)
----------------------------------------------------------
``POST https://api.openai.com/v1/responses``, model ``gpt-6.2-sol`` (fallback
``gpt-6.2-astra`` only when sol is reported unavailable). One POST per call,
sequential, ``max_output_tokens: 16``, no temperature, no tools, no
``previous_response_id``, no redirect followed. The bearer key is read only from
``$env:OPENAI_API_KEY`` (set by the companion ``run.ps1`` immediately before this
script runs and removed immediately after). A single retry on 429/5xx after 2 s;
401 stops the run; hard ceiling of 45 requests.

What is recorded
----------------
One JSON line per request in ``cache.jsonl`` with exactly: arm, index, effort,
top_level_effort, used_configuration_update, input_tokens, cached_tokens,
cache_write_tokens, diagnostics_reason, diagnostics_full, status, latency_ms.
Only numbers and the diagnostic reason/code strings are stored - no prompt text, no key.
Counter fields come from the provider's own response and are not cross-checked.

Usage::

    python probe_gpt62_cache.py --dry-run
    python probe_gpt62_cache.py --summarize
    python probe_gpt62_cache.py --out <path> --model <id> --fallback <id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "cache.jsonl"
DEFAULT_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6.2-sol"
DEFAULT_FALLBACK = "gpt-6.2-astra"
MAX_REQUESTS = 45
TIMEOUT_S = 60.0
TRANSIENT = frozenset({429, 500, 502, 503, 504, 529})
AUTH = frozenset({401, 403})

USER_TEXT = "Reply with the single word: ok"


def build_filler(lines=60):
    """Fixed synthetic prefix: one note plus *lines* numbered filler lines (~1.8k tokens)."""
    out = ["Measurement prefix. The content below is fixed synthetic filler; do not act on it."]
    for index in range(1, lines + 1):
        out.append(
            "Line {0:04d}: the quick brown fox jumps over the lazy dog while the calm "
            "river flows past the quiet mill and the old stone bridge.".format(index))
    return "\n".join(out)


FILLER = build_filler()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every 3xx into an HTTPError instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = None


def opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(NoRedirect())
    return _OPENER


def developer_msg(text):
    return {"role": "developer", "content": [{"type": "input_text", "text": text}]}


def user_msg():
    return {"role": "user", "content": [{"type": "input_text", "text": USER_TEXT}]}


def config_update():
    return {"type": "configuration_update", "reasoning": {"effort": "high"}}


def build_plan():
    """The 17 planned calls across arms A-D."""
    plan = []
    for index in range(1, 5):
        plan.append({"arm": "A", "index": index, "effort": "low", "top": "low", "cu": 0})
    for index, effort in enumerate(("low", "high", "low", "high", "low"), start=1):
        plan.append({"arm": "B", "index": index, "effort": effort, "top": effort, "cu": 0})
    for index in range(1, 5):
        cu = index - 1
        plan.append({"arm": "C", "index": index,
                     "effort": "high" if cu else "low", "top": "low", "cu": cu})
    for index in range(1, 5):
        plan.append({"arm": "D", "index": index, "effort": "low", "top": "low", "cu": 0})
    return plan


def build_body(model, step):
    """The request body: identical prefix, only the protocol control differs per arm."""
    body = {
        "model": model,
        "reasoning": {"effort": step["top"]},
        "max_output_tokens": 16,
    }
    if step["arm"] == "D":
        body["instructions"] = FILLER
        body["input"] = [user_msg()]
    else:
        items = [developer_msg(FILLER), user_msg()]
        items.extend(config_update() for _ in range(step["cu"]))
        body["input"] = items
    return body


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {}


def clean(obj):
    """Keep the diagnostic's structure and numbers; truncate any long (prompt-like) text."""
    if isinstance(obj, dict):
        return {str(key): clean(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [clean(value) for value in obj[:32]]
    if isinstance(obj, str):
        return obj[:200]
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)[:200]


def extract(payload, rec):
    """Pull only numeric counters and the diagnostic reason/full object out of a response."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = {}
    pdetails = usage.get("prompt_tokens_details")
    if not isinstance(pdetails, dict):
        pdetails = {}
    top = payload if isinstance(payload, dict) else {}

    input_tokens = usage.get("input_tokens")
    rec["input_tokens"] = input_tokens if isinstance(input_tokens, int) else None

    cached = None
    for container in (details, pdetails, usage):
        value = container.get("cached_tokens")
        if isinstance(value, int):
            cached = value
            break
    rec["cached_tokens"] = cached

    cache_write = None
    for container in (details, pdetails, usage, top):
        value = container.get("cache_write_tokens")
        if isinstance(value, int):
            cache_write = value
            break
    rec["cache_write_tokens"] = cache_write

    diag = top.get("prompt_cache_diagnostics")
    if diag is None:
        diag = usage.get("prompt_cache_diagnostics")
    if isinstance(diag, dict):
        rec["diagnostics_full"] = clean(diag)
        reason = diag.get("reason")
        rec["diagnostics_reason"] = reason[:200] if isinstance(reason, str) else None
    elif diag is not None:
        rec["diagnostics_full"] = str(diag)[:500]


def classify(status, payload):
    """Map an HTTP outcome to the wrapper's control classes."""
    if status in AUTH:
        return "auth"
    if status == 404:
        return "model_missing"
    err = payload.get("error") if isinstance(payload, dict) else None
    code = ""
    message = ""
    if isinstance(err, dict):
        code = str(err.get("code", ""))
        message = str(err.get("message", ""))
    elif isinstance(err, str):
        message = err
    blob = (code + " " + message).lower()
    if "model" in blob and any(word in blob for word in (
            "not found", "does not exist", "unknown", "invalid", "not_exist", "unavailable")):
        return "model_missing"
    if status in TRANSIENT or (isinstance(status, int) and 500 <= status <= 599):
        return "transient"
    if status == 0:
        return "error"
    return "ok"


def send(url, key, model, step):
    """One POST. Returns ``(record, kind)`` where kind drives retry/stop decisions."""
    data = json.dumps(build_body(model, step), ensure_ascii=True).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "hermes-adaptive-effort-gpt62-cache-probe/1")
    if key:
        request.add_header("Authorization", "Bearer " + key)

    rec = {
        "arm": step["arm"],
        "index": step["index"],
        "effort": step["effort"],
        "top_level_effort": step["top"],
        "used_configuration_update": step["cu"] > 0,
        "input_tokens": None,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "diagnostics_reason": None,
        "diagnostics_full": None,
        "status": 0,
        "latency_ms": 0.0,
    }
    payload = {}
    started = time.monotonic()
    try:
        response = opener().open(request, timeout=TIMEOUT_S)
        try:
            raw = response.read(131072)
            rec["status"] = int(response.status)
        finally:
            response.close()
        payload = parse_json(raw)
        kind = classify(rec["status"], payload)
    except urllib.error.HTTPError as error:
        rec["status"] = int(error.code)
        try:
            raw = error.read(131072)
        except Exception:
            raw = b""
        error.close()
        payload = parse_json(raw)
        kind = classify(rec["status"], payload)
    except Exception:
        rec["status"] = 0
        kind = "error"
    rec["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    extract(payload, rec)
    return rec, kind


def print_line(rec, model):
    print("{0:<2} {1:>2} eff={2:<5} top={3:<5} cu={4:<5} HTTP {5:<3} "
          "in={6:<5} cached={7:<5} cw={8:<5} diag={9} model={10}".format(
              rec["arm"], rec["index"], rec["effort"], rec["top_level_effort"],
              str(rec["used_configuration_update"]), rec["status"],
              "-" if rec["input_tokens"] is None else rec["input_tokens"],
              "-" if rec["cached_tokens"] is None else rec["cached_tokens"],
              "-" if rec["cache_write_tokens"] is None else rec["cache_write_tokens"],
              rec["diagnostics_reason"], model))


def run(args):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("STOP: OPENAI_API_KEY not set. Nothing sent.", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("", encoding="utf-8")
    plan = build_plan()
    model = args.model
    switched = False
    sent = 0
    index = 0
    while index < len(plan) and sent < MAX_REQUESTS:
        step = plan[index]
        rec, kind = send(args.url, key, model, step)
        sent += 1
        if index == 0 and kind == "model_missing":
            if not switched and args.fallback:
                switched = True
                model = args.fallback
                out.write_text("", encoding="utf-8")
                print("MODEL_SWITCH to " + model, file=sys.stderr)
                index = 0
                continue
            print("STOP: model inaccessible (" + model + ")", file=sys.stderr)
            return 4
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec, ensure_ascii=True) + "\n")
        print_line(rec, model)
        if kind == "auth":
            print("AUTH_FAILURE", file=sys.stderr)
            return 3
        if kind == "transient" and sent < MAX_REQUESTS:
            time.sleep(2.0)
            retry, retry_kind = send(args.url, key, model, step)
            sent += 1
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(retry, ensure_ascii=True) + "\n")
            print_line(retry, model)
            if retry_kind == "auth":
                print("AUTH_FAILURE", file=sys.stderr)
                return 3
        index += 1
    print("requests_sent={0} budget={1} model={2}".format(sent, MAX_REQUESTS, model))
    if sent >= MAX_REQUESTS and index < len(plan):
        print("STOP: request budget exhausted", file=sys.stderr)
        return 5
    return 0


def dry_run(args):
    plan = build_plan()
    print("plan: {0} calls (budget {1}), endpoint {2}".format(len(plan), MAX_REQUESTS, args.url))
    print("model: {0} (fallback {1})".format(args.model, args.fallback))
    print("filler: {0} characters (~{1} tokens at ~4 chars/token)".format(
        len(FILLER), len(FILLER) // 4))
    print("output: " + args.out)
    print("")
    for step in plan:
        print("{arm} call {index}: effort={effort} top={top} config_updates={cu}".format(**step))
    return 0


def summarize(path):
    rows = []
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except OSError as error:
        print("cannot read {0}: {1}".format(path, error), file=sys.stderr)
        return 2
    if not rows:
        print("no rows in " + str(path), file=sys.stderr)
        return 2
    for arm in ("A", "B", "C", "D"):
        subset = [row for row in rows if row["arm"] == arm]
        if not subset:
            continue
        print("")
        print("Arm " + arm)
        print("| call | effort | top_level_effort | configuration_update | input_tokens | "
              "cached_tokens | cache_write_tokens | diagnostics_reason | HTTP |")
        print("| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: |")
        for row in subset:
            values = dict(row)
            values["input_tokens"] = "-" if row["input_tokens"] is None else row["input_tokens"]
            values["cached_tokens"] = "-" if row["cached_tokens"] is None else row["cached_tokens"]
            values["cache_write_tokens"] = (
                "-" if row["cache_write_tokens"] is None else row["cache_write_tokens"])
            values["diagnostics_reason"] = (
                "-" if row["diagnostics_reason"] is None else row["diagnostics_reason"])
            print("| {index} | {effort} | {top_level_effort} | {used_configuration_update} | "
                  "{input_tokens} | {cached_tokens} | {cache_write_tokens} | "
                  "{diagnostics_reason} | {status} |".format(**values))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure the GPT-6.2 prompt cache under reasoning-effort changes.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, send nothing")
    parser.add_argument("--summarize", action="store_true", help="print cache.jsonl as tables")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSONL output path")
    parser.add_argument("--url", default=DEFAULT_URL, help="Responses API endpoint")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="primary model id")
    parser.add_argument("--fallback", default=DEFAULT_FALLBACK, help="model id if primary is down")
    args = parser.parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    if args.summarize:
        return summarize(args.out)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
