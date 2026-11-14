"""Live measurement of the OpenCode Go prompt cache under effort changes.

Why this exists
---------------
The plugin may change ``reasoning_effort`` between turns on a cached route. Before it
can, we need to know *why* the Go gateway drops the prompt cache on such a change.
Three hypotheses are on the table:

* **H1 partition** — the cache entry is keyed per effort, so returning to a level used
  before hits again, and a never-used level misses exactly once.
* **H2 invalidation** — any change flushes the shared entry, so even a return misses.
* **H3 affinity** — a hit needs the same ``x-opencode-session`` *and* the same effort,
  so a new session at an unchanged effort still misses once.

Transport (measured on this machine, 2026-11-14; no secret is ever printed)
--------------------------------------------------------------------------
* ``POST https://opencode.ai/zen/go/v1/chat/completions``, model
  ``deepseek-v4.1-flash`` (the bare slug: the prefixed
  ``deepseek/deepseek-v4.1-flash`` is rejected with HTTP 400 by this gateway).
* credential from ``$env:OPENCODE_GO_API_KEY`` first, else the OpenCode CLI store
  (``opencode-go`` / ``key``). Only sent as ``Authorization: Bearer``. Never logged.
* fixed ``x-opencode-session`` per condition; condition D rotates it on purpose.
* one POST per call, ``max_tokens: 4``, ``temperature: 0``, no streaming, 30 s timeout,
  no redirect followed. A single retry only on 429/5xx, after 2 s, and only if the
  40-request budget still allows it. Any 401 stops the run.

The prompt is a fixed, generated prefix of roughly 2,050 tokens (one fixed system note
plus 70 numbered filler lines). It contains no user data, is byte-identical across every
call, and is never written to the log.

What is recorded
----------------
One JSON line per request in ``docs/validation/runs/opencode-go-cache-20261114/cache.jsonl``::

    {ts, condition, index, session, effort, max_tokens, temperature, status,
     cached_tokens, prompt_tokens, latency_ms}

``cached_tokens`` comes from the provider's own ``usage.prompt_tokens_details.cached_tokens``
(falling back to ``usage.cached_tokens`` then ``usage.prompt_cache_hit_tokens``). It is the
gateway's counter, not an independent measurement. No prompt text, no key, no response body.

Usage::

    python scripts/probe_opencode_go_cache.py --dry-run
    python scripts/probe_opencode_go_cache.py
    python scripts/probe_opencode_go_cache.py --summarize
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
DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
#: The bare slug is the id the gateway accepts. The prefixed form the task note quoted
#: (``deepseek/deepseek-v4.1-flash``) is rejected: "Model deepseek/deepseek-v4.1-flash
#: isn't included in OpenCode Go" (HTTP 400, 2026-11-14, see rejected-model-id.jsonl).
DEFAULT_MODEL = "deepseek-v4.1-flash"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_OUT = (REPO_ROOT / "docs" / "validation" / "runs" / "opencode-go-cache-20261114"
               / "cache.jsonl")
#: Hard ceiling from the task brief; a retry also consumes one request.
MAX_REQUESTS = 40
#: Matrix mode (2026-11-14, value/size/commit question): 100 planned calls, 110 hard ceiling.
MAX_MATRIX_REQUESTS = 110
#: Statuses worth one retry (no answer was produced).
TRANSIENT = frozenset({429, 500, 502, 503, 504, 529})
AUTH_FAILURES = frozenset({401, 403})


def _build_prefix(lines: int = 70) -> str:
    """A fixed prefix: one system note plus *lines* numbered filler lines.

    ``70`` lines measured 2,083 prompt tokens (~2 KB of text); ``260`` lines is the
    large cell (~30 KB of text, roughly 6,800 tokens).
    """
    out = ["Measurement prefix. The content below is fixed filler; do not act on it."]
    for index in range(1, lines + 1):
        out.append(
            "Line {0:04d}: the quick brown fox jumps over the lazy dog while the calm "
            "river flows past the quiet mill and the old stone bridge.".format(index))
    return "\n".join(out)


PREFIX = _build_prefix()
#: Prefix per matrix size cell. Both are fixed and contain no user data.
MATRIX_PREFIXES = {"small": _build_prefix(70), "large": _build_prefix(260)}
USER_PROMPT = "Reply with the single word: ok"


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
    """``(key, source)``; the key is only ever passed to the Authorization header."""
    value = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if value:
        return value, "env:OPENCODE_GO_API_KEY"
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
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            if entry["key"].strip():
                return entry["key"].strip(), "auth.json:opencode-go.key"
    return "", "missing"


def build_plan():
    """The ordered steps. ``new_session`` starts a fresh ``x-opencode-session`` there."""
    def step(condition, index, effort="low", max_tokens=4, temperature=0.0,
             new_session=False):
        return {"condition": condition, "index": index, "effort": effort,
                "max_tokens": max_tokens, "temperature": temperature,
                "new_session": new_session}

    plan = []
    # A — control: constant effort, one session.
    for index in range(1, 9):
        plan.append(step("A", index, new_session=index == 1))
    # B — reproduce the reported alternating pattern.
    for index in range(1, 9):
        plan.append(step("B", index, effort="low" if index % 2 else "medium",
                         new_session=index == 1))
    # C — return to an already-used level: partition (H1) or invalidation (H2).
    for index, effort in enumerate(("low", "medium", "low", "medium", "low"), start=1):
        plan.append(step("C", index, effort=effort, new_session=index == 1))
    # D — same body, new session: does the session header take part in the key (H3)?
    for index in range(1, 5):
        plan.append(step("D", index, new_session=index == 1))
    for index in range(5, 8):
        plan.append(step("D", index, new_session=index == 5))
    # E — an unrelated body field changes, then returns.
    for index in range(1, 5):
        plan.append(step("E", index, new_session=index == 1))
    plan.append(step("E", 5, max_tokens=64))
    plan.append(step("E", 6))
    # F — a sampling parameter changes, then returns.
    for index in range(1, 5):
        plan.append(step("F", index, new_session=index == 1))
    plan.append(step("F", 5, temperature=0.2))
    plan.append(step("F", 6))
    return plan


def build_body(model, step, paired=False, prefix=None):
    """The request body: the fixed prefix, one effort value, one output bound."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": prefix if prefix is not None else PREFIX},
                     {"role": "user", "content": USER_PROMPT}],
        "max_tokens": step.get("max_tokens", 4),
        "temperature": step.get("temperature", 0.0),
        "stream": False,
        "reasoning_effort": step["effort"],
    }
    if paired:
        # The plugin's registered carrier for the DeepSeek Go Chat route is
        # ``paired_effort``: the effort is only meaningful with thinking enabled.
        body["thinking"] = {"type": "enabled"}
    return body


def _cached_tokens(usage):
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if isinstance(cached, int):
            return cached
    for name in ("cached_tokens", "prompt_cache_hit_tokens"):
        cached = usage.get(name)
        if isinstance(cached, int):
            return cached
    return None


def _prompt_tokens(usage):
    if isinstance(usage, dict) and isinstance(usage.get("prompt_tokens"), int):
        return usage["prompt_tokens"]
    return None


def _error_code(payload):
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, str):
        return err[:200]
    if not isinstance(err, dict):
        return None
    for name in ("code", "message"):
        value = err.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return None


def send(base_url, key, model, step, session, paired=False, prefix=None):
    """One POST. Returns ``(record, transient)``; ``transient`` asks for the one retry."""
    url = base_url.rstrip("/") + "/chat/completions"
    data = json.dumps(build_body(model, step, paired, prefix)).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "hermes-adaptive-effort-cache-probe/1")
    request.add_header("x-opencode-session", session)
    if key:
        request.add_header("Authorization", "Bearer " + key)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "condition": step["condition"],
        "index": step["index"],
        "session": session,
        "effort": step["effort"],
        "max_tokens": step.get("max_tokens", 4),
        "temperature": step.get("temperature", 0.0),
        "status": 0,
        "cached_tokens": None,
        "prompt_tokens": None,
        "latency_ms": 0.0,
    }
    transient = False
    started = time.monotonic()
    try:
        response = _opener().open(request, timeout=DEFAULT_TIMEOUT_S)
        try:
            raw = response.read(65536)
            record["status"] = int(response.status)
        finally:
            response.close()
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            payload = {}
        usage = payload.get("usage") if isinstance(payload, dict) else None
        record["cached_tokens"] = _cached_tokens(usage)
        record["prompt_tokens"] = _prompt_tokens(usage)
    except urllib.error.HTTPError as error:
        record["status"] = int(error.code)
        transient = int(error.code) in TRANSIENT
        try:
            raw = error.read(65536)
        except Exception:
            raw = b""
        error.close()
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            payload = {}
        code = _error_code(payload)
        if code:
            record["error_code"] = code.replace(key, "<redacted>") if key else code[:200]
    except Exception as error:  # timeout, DNS, TLS: no status at all
        record["status"] = 0
        record["error_code"] = type(error).__name__
    record["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    return record, transient


def _print_record(record):
    cached = record["cached_tokens"]
    prompt = record["prompt_tokens"]
    verdict = "?" if cached is None else ("hit" if cached >= 1 else "miss")
    print("{:<10} {:>2}  {:<6} max={:<2} temp={:<3} {:<10} {:>5} -> cached={:<5} "
          "prompt={} {}".format(
              record["condition"], record["index"], record["effort"],
              record["max_tokens"], record["temperature"], record["session"][:8],
              record["status"], "-" if cached is None else cached,
              "-" if prompt is None else prompt, verdict))


def summarize(path):
    """Print the measured rows grouped by condition, as markdown, from the JSONL."""
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
    current = None
    print("| condition | call | effort | max_tokens | temperature | session | HTTP | "
          "cached_tokens | prompt_tokens | verdict |")
    print("| --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |")
    for row in rows:
        if row["condition"] != current:
            current = row["condition"]
        cached = row["cached_tokens"]
        verdict = "error" if row["status"] != 200 else (
            "?" if cached is None else ("hit" if cached >= 1 else "miss"))
        print("| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} | {9} |".format(
            row["condition"], row["index"], row["effort"], row["max_tokens"],
            row["temperature"], row["session"][:8], row["status"],
            "-" if cached is None else cached,
            "-" if row["prompt_tokens"] is None else row["prompt_tokens"], verdict))
    return 0


def build_matrix_plan():
    """The 2026-11-14 value/size/commit matrix: 100 planned calls, 23 cells."""
    def step(size, condition, rep, index, effort, delay_s=0.0, new_session=False):
        return {"size": size, "condition": condition, "rep": rep, "index": index,
                "effort": effort, "delay_s": delay_s, "new_session": new_session}

    plan = []
    # Arm 1 — small prefix, three repetitions, every cell on a fresh session.
    arm1 = {
        "A": ["low"] * 6,
        "B": ["low", "max"] * 3,
        "C": ["none", "low"] * 3,
        "D": ["low"] * 6,          # a new session starts at index 4
    }
    for rep in (1, 2, 3):
        for condition in ("A", "B", "C", "D"):
            for index, effort in enumerate(arm1[condition], start=1):
                plan.append(step("small", condition, rep, index, effort,
                                 new_session=index == 1
                                 or (condition == "D" and index == 4)))
    # Arm 2 — large prefix, one repetition per cell.
    arm2 = {
        "E": ["low"] * 4,
        "F": ["low", "max"] * 2,
        "G": ["none", "low"] * 2,
        "H": ["low"] * 4,          # a new session starts at index 3
    }
    for condition in ("E", "F", "G", "H"):
        for index, effort in enumerate(arm2[condition], start=1):
            plan.append(step("large", condition, 1, index, effort,
                             new_session=index == 1
                             or (condition == "H" and index == 3)))
    # Arm 3 — large prefix, the second call delayed: change (I) and control (J).
    for delay in (0.0, 10.0, 30.0):
        for condition, efforts in (("I", ["low", "max"]), ("J", ["low", "low"])):
            for index, effort in enumerate(efforts, start=1):
                plan.append(step("large", condition, int(delay), index, effort,
                                 delay_s=delay if index == 2 else 0.0,
                                 new_session=index == 1))
    return plan


def _matrix_row(step, record):
    """Exactly the fields the brief asks for, plus ``error_code`` when the call failed."""
    row = {
        "size": step["size"],
        "condition": step["condition"],
        "rep": step["rep"],
        "index": step["index"],
        "session": record["session"],
        "effort": step["effort"],
        "delay_s": step["delay_s"],
        "status": record["status"],
        "cached_tokens": record["cached_tokens"],
        "prompt_tokens": record["prompt_tokens"],
        "latency_ms": record["latency_ms"],
    }
    if record.get("error_code"):
        row["error_code"] = record["error_code"]
    return row


def summarize_matrix(path):
    """Print the matrix JSONL as a markdown table, grouped by cell."""
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
    print("| size | cond | rep | call | effort | delay_s | HTTP | cached | prompt | verdict |")
    print("| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |")
    for row in rows:
        cached = row["cached_tokens"]
        verdict = "error" if row["status"] != 200 else (
            "?" if cached is None else ("hit" if cached >= 1 else "miss"))
        print("| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} | {9} |".format(
            row["size"], row["condition"], row["rep"], row["index"], row["effort"],
            row["delay_s"], row["status"],
            "-" if cached is None else cached,
            "-" if row["prompt_tokens"] is None else row["prompt_tokens"], verdict))
    return 0


def run_matrix(args):
    """Run the value/size/commit matrix sequentially, under the 110-request ceiling."""
    plan = build_matrix_plan()
    if args.conditions:
        wanted = {name.strip().upper() for name in args.conditions.split(",") if name.strip()}
        plan = [step for step in plan if step["condition"] in wanted]
        if not plan:
            print("no condition matches " + args.conditions, file=sys.stderr)
            return 2
    if args.dry_run:
        counts = {}
        for step in plan:
            counts[(step["size"], step["condition"], step["rep"])] = \
                counts.get((step["size"], step["condition"], step["rep"]), 0) + 1
        print("matrix: {0} cells, {1} requests (budget {2}), endpoint {3}".format(
            len(counts), len(plan), MAX_MATRIX_REQUESTS,
            args.base_url.rstrip("/") + "/chat/completions"))
        print("model: " + args.model)
        for size, prefix in MATRIX_PREFIXES.items():
            print("prefix {0}: {1} characters (roughly {2} tokens at ~4.4 chars/token)".format(
                size, len(prefix), len(prefix) * 10 // 44))
        print("output: " + args.out_matrix)
        print("")
        for (size, condition, rep), count in counts.items():
            efforts = ",".join(step["effort"] for step in plan
                               if step["size"] == size and step["condition"] == condition
                               and step["rep"] == rep)
            delays = [step["delay_s"] for step in plan
                      if step["size"] == size and step["condition"] == condition]
            print("{0:>5} {1} rep{2}: {3} calls [{4}] delay={5}".format(
                size, condition, rep, count, efforts, sorted(set(delays))))
        return 0

    key, source = resolve_key()
    if not key:
        print("STOP: no OpenCode Go credential (env OPENCODE_GO_API_KEY or the CLI auth "
              "store). Nothing sent.", file=sys.stderr)
        return 2

    out_path = Path(args.out_matrix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print("credential source: " + source)
    print("output: " + str(out_path))
    print("")
    print("size  cond rep  # effort delay    HTTP cached/prompt")
    sent = 0
    session = str(uuid.uuid4())
    stop_reason = None
    for step in plan:
        if step["new_session"]:
            session = str(uuid.uuid4())
        if sent >= MAX_MATRIX_REQUESTS:
            stop_reason = "request budget exhausted"
            break
        if step["delay_s"]:
            time.sleep(float(step["delay_s"]))
        record, transient = send(args.base_url, key, args.model, step, session,
                                 prefix=MATRIX_PREFIXES[step["size"]])
        sent += 1
        row = _matrix_row(step, record)
        with out_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        print("{:>5} {:<2} rep{:<2} {:>2} {:<6} delay={:<4} {:>5} {}/{} {}".format(
            step["size"], step["condition"], step["rep"], step["index"], step["effort"],
            step["delay_s"], record["status"],
            "-" if record["cached_tokens"] is None else record["cached_tokens"],
            "-" if record["prompt_tokens"] is None else record["prompt_tokens"],
            "HIT" if (record["cached_tokens"] or 0) >= 1 else "miss"))
        if record["status"] in AUTH_FAILURES:
            stop_reason = "authentication failed (HTTP {0})".format(record["status"])
            break
        if sent == 1 and record["status"] != 200:
            stop_reason = "first request failed (HTTP {0})".format(record["status"])
            break
        if transient and sent < MAX_MATRIX_REQUESTS:
            time.sleep(2.0)
            retry, _ = send(args.base_url, key, args.model, step, session,
                            prefix=MATRIX_PREFIXES[step["size"]])
            sent += 1
            row = _matrix_row(step, retry)
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
            print("  retry -> {0} cached={1}".format(
                retry["status"], retry["cached_tokens"]))
    print("")
    print("{0} request(s) sent (budget {1})".format(sent, MAX_MATRIX_REQUESTS))
    if stop_reason:
        print("STOP: " + stop_reason, file=sys.stderr)
        return 3
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure the OpenCode Go prompt cache under effort changes.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and the prefix size, send nothing")
    parser.add_argument("--summarize", action="store_true",
                        help="print the measured JSONL as a markdown table")
    parser.add_argument("--conditions", default="",
                        help="comma-separated condition letters to run (default: all)")
    parser.add_argument("--paired", action="store_true",
                        help="also send thinking={'type': 'enabled'} (the plugin's "
                             "registered carrier for this route)")
    parser.add_argument("--matrix", action="store_true",
                        help="run the 2026-11-14 value/size/commit matrix instead of the "
                             "first-pass conditions")
    parser.add_argument("--summarize-matrix", action="store_true",
                        help="print the matrix JSONL as a markdown table")
    parser.add_argument("--out-matrix", default=str(DEFAULT_OUT.with_name("cache-matrix.jsonl")),
                        help="matrix JSONL path (default: cache-matrix.jsonl)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    if args.summarize:
        return summarize(args.out)
    if args.summarize_matrix:
        return summarize_matrix(args.out_matrix)
    if args.matrix:
        return run_matrix(args)

    plan = build_plan()
    wanted = {name.strip().upper() for name in args.conditions.split(",") if name.strip()}
    if wanted:
        plan = [step for step in plan if step["condition"] in wanted]
        if not plan:
            print("no condition matches " + args.conditions, file=sys.stderr)
            return 2
    if args.dry_run:
        print("plan: {0} conditions, {1} requests (budget {2}), endpoint {3}".format(
            len({step["condition"] for step in plan}), len(plan), MAX_REQUESTS,
            args.base_url.rstrip("/") + "/chat/completions"))
        print("model: " + args.model)
        print("prefix: {0} characters (roughly {1} tokens at ~4 chars/token)".format(
            len(PREFIX), len(PREFIX) // 4))
        print("output: " + args.out)
        print("")
        for name in sorted({step["condition"] for step in plan}):
            steps = [step for step in plan if step["condition"] == name]
            detail = ", ".join(
                "{0}/max{1}/temp{2}{3}".format(
                    step["effort"], step["max_tokens"], step["temperature"],
                    "/new-session" if step["new_session"] else "")
                for step in steps)
            print("{0}: {1}".format(name, detail))
        return 0

    key, source = resolve_key()
    if not key:
        print("STOP: no OpenCode Go credential (env OPENCODE_GO_API_KEY or the CLI "
              "auth store). Nothing sent.", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print("credential source: " + source)
    print("prefix: {0} characters".format(len(PREFIX)))
    print("output: " + str(out_path))
    print("")

    sent = 0
    session = str(uuid.uuid4())
    stop_reason = None
    for step in plan:
        if step["new_session"]:
            session = str(uuid.uuid4())
        if sent >= MAX_REQUESTS:
            stop_reason = "request budget exhausted"
            break
        record, transient = send(args.base_url, key, args.model, step, session, args.paired)
        sent += 1
        with out_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        _print_record(record)
        if record["status"] in AUTH_FAILURES:
            stop_reason = "authentication failed (HTTP {0})".format(record["status"])
            break
        if sent == 1 and record["status"] != 200:
            # Nothing is learned from 40 identical failures; report the first one.
            stop_reason = "first request failed (HTTP {0})".format(record["status"])
            break
        if transient and sent < MAX_REQUESTS:
            time.sleep(2.0)
            record, _ = send(args.base_url, key, args.model, step, session, args.paired)
            sent += 1
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
            _print_record(record)
            if record["status"] in AUTH_FAILURES:
                stop_reason = "authentication failed (HTTP {0})".format(record["status"])
                break

    print("")
    print("{0} request(s) sent (budget {1})".format(sent, MAX_REQUESTS))
    if stop_reason:
        print("STOP: " + stop_reason, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


