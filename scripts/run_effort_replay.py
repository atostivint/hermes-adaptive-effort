#!/usr/bin/env python3
"""Manual effort replay on the synthetic replay corpus (billed; never run in CI).

Runs every corpus item against ONE target route at every effort level of that route,
N times each, checks the answers mechanically and records the cheapest level whose answers
pass (see ``replay_labels.derive_label``). The derived label is evidence about the target
model's behaviour on synthetic, checkable tasks: it is not ground truth for real work.

Without ``--send`` this only validates the corpus and prints the planned call count; it
makes no network request. ``~/.hermes/config.yaml`` is never read. The API key is resolved
through ``agent.secret_scope`` then the environment variable named by ``--key-env`` and is
never printed or written. The result holds ids and numbers only: no prompts, answers, keys,
hosts or endpoints.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import re
import sys
import urllib.error
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_checks  # noqa: E402
import replay_labels  # noqa: E402
import run_scorer_eval as scorer_eval  # noqa: E402

ROOT = scorer_eval.ROOT
CORPUS_PATH = ROOT / "tests" / "fixtures" / "effort_replay_corpus.json"
CORPUS_SCHEMA = "hermes-adaptive-effort.replay-corpus.v1"
RESULT_SCHEMA = "hermes-adaptive-effort.effort-replay.v1"
RESULT_DIR_NAME = "effort-replay"
REPO_COPY_DIR = scorer_eval.REPO_COPY_DIR
DEFAULT_PROVIDER = "opencode-go"
DEFAULT_MODEL = "deepseek-v4.1-flash"
DEFAULT_API_MODE = "chat_completions"
#: Same host and path the OpenCode Go chat route is reached on (docs/reviews/live-iris-use-cases-20261003.md).
DEFAULT_ENDPOINT = "https://opencode.ai/zen/go/v1/chat/completions"
REJECTION_STATUSES = frozenset({400, 422})
FATAL_STATUSES = frozenset({401, 403})
#: A level is declared rejected after this many rejections with no accepted response.
REJECTION_LIMIT = 2
MIN_SAMPLES = 3


class TargetRejected(Exception):
    """The provider refused the effort value (HTTP 400/422); not a failed check."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"http_{status}")
        self.status, self.code = status, code


class TargetError(Exception):
    """Any other target failure; ``fatal`` stops the whole run (bad credentials)."""

    def __init__(self, code: str, fatal: bool = False) -> None:
        super().__init__(code)
        self.code, self.fatal = code, fatal


CallTarget = Callable[[str, str], Tuple[str, Dict[str, Any]]]


def validate_replay_corpus(corpus: Any, levels: Sequence[str]) -> List[str]:
    problems = scorer_eval.validate_corpus(corpus, levels)
    if isinstance(corpus, dict) and corpus.get("schema") != CORPUS_SCHEMA:
        problems.append("corpus schema mismatch")
    for item in corpus.get("items", []) if isinstance(corpus, dict) else []:
        if isinstance(item, dict):
            problems.extend(f"{item.get('id')}: {p}"
                            for p in replay_checks.validate_check(item.get("check")))
    return problems


def _safe_code(value: Any) -> str:
    text = re.sub(r"[^a-z0-9_.-]", "", str(value or "").lower())[:40]
    return text or "unspecified"


def make_opencode_go_target(endpoint: str, key: str, model: str, *, max_tokens: int = 8192,
                            timeout: float = 120.0,
                            transport: Optional[Callable[..., Any]] = None) -> CallTarget:
    """Chat-completions caller for the OpenCode Go ``paired_effort`` route.

    The body carries ``thinking: {"type": "enabled"}`` plus top-level ``reasoning_effort``,
    the same pair ``middleware`` writes for this route. Redirects are never followed and
    nothing is retried: every attempt is billed once.
    """
    http = scorer_eval.load_plugin("scorer_http")
    send = transport or http.default_transport

    def call(prompt: str, level: str) -> Tuple[str, Dict[str, Any]]:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "enabled"}, "reasoning_effort": level,
                "max_tokens": max_tokens}
        request = http.build_request(endpoint, body, key=key,
                                     user_agent="hermes-adaptive-effort-replay")
        try:
            response = send(request, timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            code = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("error")
                code = (detail.get("code") or detail.get("type")) if isinstance(detail, dict) \
                    else ""
            except Exception:
                pass
            http.close_response(exc)
            if status in REJECTION_STATUSES:
                raise TargetRejected(status, _safe_code(code)) from None
            raise TargetError(f"http_{status}", fatal=status in FATAL_STATUSES) from None
        except urllib.error.URLError as exc:
            raise TargetError("timeout" if isinstance(getattr(exc, "reason", None), TimeoutError)
                              else "transport_error") from None
        except TimeoutError:
            raise TargetError("timeout") from None
        except OSError:
            raise TargetError("transport_error") from None
        payload, failure = http.read_json(response)
        if failure or not isinstance(payload, dict):
            raise TargetError(failure or "malformed_response")
        try:
            content = payload["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise TargetError("malformed_response") from None
        usage = payload.get("usage")
        return (content if isinstance(content, str) else ""), (usage if isinstance(usage, dict)
                                                               else {})
    return call


def _tokens(usage: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    def as_int(value: Any) -> Optional[int]:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 \
            else None
    details = usage.get("completion_tokens_details")
    reasoning = as_int(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    return as_int(usage.get("prompt_tokens")), as_int(usage.get("completion_tokens")), reasoning


def _mean(values: Sequence[int]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def run_replay(corpus: Dict[str, Any], corpus_sha256: str, route: Dict[str, str],
               levels: Sequence[str], n: int, call_target: CallTarget, *,
               threshold: float = 1.0, limit: Optional[int] = None,
               max_calls: Optional[int] = None, max_cost: Optional[Decimal] = None,
               prices: Optional[Tuple[Decimal, Decimal]] = None,
               now: Optional[_datetime.datetime] = None) -> Dict[str, Any]:
    """Replay the corpus item by item; build the result document. Never raises on target errors."""
    levels = tuple(levels)
    items = list(corpus["items"])
    if limit is not None:
        items = items[:max(0, limit)]
    status = {level: {"accepted": 0, "rejected": 0, "failed": 0, "rejection_codes": {}}
              for level in levels}
    failures: Dict[str, int] = {}
    totals_tokens = {level: 0 for level in levels}
    calls, spent, aborted = 0, Decimal(0), None
    rows: List[Dict[str, Any]] = []

    def dead(level: str) -> bool:
        s = status[level]
        return s["rejected"] >= REJECTION_LIMIT and s["accepted"] == 0

    for item in items:
        outcomes = {level: [] for level in levels}
        out_tokens = {level: [] for level in levels}
        reasoning_tokens = {level: [] for level in levels}
        for level in levels:
            for _ in range(n):
                if dead(level):
                    outcomes[level].append(replay_labels.REJECTED)
                    continue
                if max_calls is not None and calls >= max_calls:
                    aborted = "max_calls"
                    break
                calls += 1
                try:
                    text, usage = call_target(item["prompt"], level)
                except TargetRejected as exc:
                    status[level]["rejected"] += 1
                    codes = status[level]["rejection_codes"]
                    key = f"http_{exc.status}:{_safe_code(exc.code)}"
                    codes[key] = codes.get(key, 0) + 1
                    outcomes[level].append(replay_labels.REJECTED)
                    continue
                except TargetError as exc:
                    status[level]["failed"] += 1
                    failures[exc.code] = failures.get(exc.code, 0) + 1
                    outcomes[level].append(replay_labels.FAIL)
                    if exc.fatal:
                        aborted = "fatal_" + _safe_code(exc.code)
                        break
                    continue
                status[level]["accepted"] += 1
                prompt_t, completion_t, reasoning_t = _tokens(usage)
                if completion_t is not None:
                    out_tokens[level].append(completion_t)
                    totals_tokens[level] += completion_t
                if reasoning_t is not None:
                    reasoning_tokens[level].append(reasoning_t)
                outcomes[level].append(replay_labels.PASS
                                       if replay_checks.run_check(item["check"], text)
                                       else replay_labels.FAIL)
                if max_cost is not None and prices is not None:
                    if prompt_t is None or completion_t is None:
                        aborted = "usage_missing"
                        break
                    spent += (Decimal(prompt_t) * prices[0] + Decimal(completion_t) * prices[1]) \
                        / Decimal(1_000_000)
                    if spent >= max_cost:
                        aborted = "max_cost"
                        break
            if aborted:
                break
        if aborted:
            break  # the item in progress is incomplete and is dropped from the result
        derived = replay_labels.derive_label(levels, outcomes, n, threshold)
        rows.append({
            "id": item["id"], "expected": item["expected"],
            "pass_rate": derived["pass_rate"], "label": derived["label"],
            "dropped": derived["dropped"], "non_monotonic": derived["non_monotonic"],
            "floor": derived["floor"], "rejected_levels": derived["rejected_levels"],
            "tokens": {level: _mean(out_tokens[level]) for level in levels},
            "reasoning_tokens": {level: _mean(reasoning_tokens[level]) for level in levels},
        })
    stamp = (now or _datetime.datetime.now(_datetime.timezone.utc)).astimezone(
        _datetime.timezone.utc)
    labelled = [row for row in rows if row["label"]]
    distribution: Dict[str, int] = {}
    for row in labelled:
        distribution[row["label"]] = distribution.get(row["label"], 0) + 1
    return {
        "schema": RESULT_SCHEMA,
        "target": dict(route),
        "corpus_version": corpus["version"],
        "corpus_sha256": corpus_sha256,
        "run_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "levels": list(levels),
        "samples_per_level": n,
        "threshold": threshold,
        "items_planned": len(items),
        "items": len(rows),
        "aborted": aborted,
        "calls": calls,
        "estimated_cost_usd": str(spent) if prices is not None else None,
        "failures": dict(sorted(failures.items())),
        "level_status": status,
        "completion_tokens_total": totals_tokens,
        "labelled": len(labelled),
        "dropped": sum(1 for row in rows if row["dropped"]),
        "non_monotonic": sum(1 for row in rows if row["non_monotonic"]),
        "label_distribution": dict(sorted(distribution.items())),
        "results": rows,
    }


def result_filename(result: Dict[str, Any]) -> str:
    model = "".join(ch for ch in result["target"]["model"] if ch.isalnum() or ch in "-_.") \
        or "model"
    return (f"effort-replay-{model}-{result['corpus_sha256'][:8]}-"
            f"{result['run_at'][:10].replace('-', '')}.json")


def write_result(result: Dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / result_filename(result)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    import os
    os.replace(temp, target)
    return target


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-mode", default=DEFAULT_API_MODE)
    parser.add_argument("--levels", help="comma-separated subset of the route's levels")
    parser.add_argument("-n", "--samples", type=int, default=MIN_SAMPLES,
                        help=f"samples per item and level (>= {MIN_SAMPLES})")
    parser.add_argument("--threshold", type=float, default=1.0,
                        help="pass-rate a level needs to count as passing (default: all N)")
    parser.add_argument("--limit", type=int, help="replay only the first N items")
    parser.add_argument("--corpus", default=str(CORPUS_PATH))
    parser.add_argument("--send", action="store_true",
                        help="actually call the target (billed); without it nothing is sent")
    parser.add_argument("--max-calls", type=int, help="abort before exceeding N target calls")
    parser.add_argument("--max-cost", type=Decimal,
                        help="abort once the estimated cost (USD) reaches this; needs both prices")
    parser.add_argument("--price-in-per-m", type=Decimal, help="USD per million prompt tokens")
    parser.add_argument("--price-out-per-m", type=Decimal,
                        help="USD per million completion tokens")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help="chat-completions URL of the target route")
    parser.add_argument("--key-env", help="name of the API key variable (required with --send)")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--out-dir", help="override the plugin data directory (tests/offline)")
    parser.add_argument("--repo-copy", action="store_true",
                        help="also write the result to docs/validation/runs/")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, out: Any = None,
         call_target: Optional[CallTarget] = None,
         now: Optional[_datetime.datetime] = None) -> int:
    out = out or sys.stdout
    scorer_eval.ensure_hermes_on_path()
    effort_module = scorer_eval.load_plugin("effort")
    args = make_parser().parse_args(argv)
    route_levels = effort_module.route_choice_levels(args.provider, args.model, args.api_mode)
    if not route_levels:
        print("error: no registered effort vocabulary for this route (or Hermes source missing: "
              "set HERMES_SOURCE_ROOT)", file=out)
        return 2
    levels = tuple(route_levels)
    if args.levels:
        wanted = [part.strip().lower() for part in args.levels.split(",") if part.strip()]
        if not wanted or not set(wanted) <= set(route_levels):
            print(f"error: --levels must be a non-empty subset of {', '.join(route_levels)}",
                  file=out)
            return 2
        levels = tuple(level for level in route_levels if level in wanted)
    if args.samples < MIN_SAMPLES or not 0.0 < args.threshold <= 1.0:
        print(f"error: --samples must be >= {MIN_SAMPLES} and --threshold in (0, 1]", file=out)
        return 2
    corpus, digest = scorer_eval.load_corpus(Path(args.corpus))
    problems = validate_replay_corpus(corpus, scorer_eval.load_plugin("effort").CHOICE_LEVELS)
    if problems:
        print("error: corpus invalid: " + "; ".join(problems[:5]), file=out)
        return 2
    count = len(corpus["items"]) if args.limit is None else min(len(corpus["items"]),
                                                                 max(0, args.limit))
    planned = count * len(levels) * args.samples
    print(f"corpus {corpus['version']} ({count} of {len(corpus['items'])} items), route "
          f"{args.provider}/{args.model} ({args.api_mode}), levels {','.join(levels)}, "
          f"{args.samples} samples", file=out)
    if not args.send:
        print(f"planned target calls: {planned} (nothing sent; add --send to run, billed by the "
              "target provider)", file=out)
        return 0
    prices = None
    if args.max_cost is not None or args.price_in_per_m is not None \
            or args.price_out_per_m is not None:
        if args.price_in_per_m is None or args.price_out_per_m is None:
            print("error: --max-cost needs --price-in-per-m and --price-out-per-m", file=out)
            return 2
        prices = (args.price_in_per_m, args.price_out_per_m)
    if args.max_calls is None and args.max_cost is None:
        print("error: --send needs --max-calls or --max-cost (hard billing guard)", file=out)
        return 2
    if call_target is None:
        if (args.provider, args.api_mode) != (DEFAULT_PROVIDER, DEFAULT_API_MODE):
            print("error: only the opencode-go chat_completions transport is implemented",
                  file=out)
            return 2
        if not args.key_env:
            print("error: --key-env NAME is required with --send", file=out)
            return 2
        key = scorer_eval.load_plugin("scorer_http").read_secret(args.key_env)
        if not key:
            print("error: the key variable is empty or unset", file=out)
            return 2
        call_target = make_opencode_go_target(args.endpoint, key, args.model,
                                              max_tokens=args.max_tokens, timeout=args.timeout_s)
    if args.out_dir:
        directory = Path(args.out_dir)
    else:
        try:
            directory = scorer_eval.default_result_dir().parent / RESULT_DIR_NAME
        except Exception:
            print("error: plugin data directory unavailable (Hermes not importable); "
                  "pass --out-dir", file=out)
            return 2
    route = {"provider": args.provider, "model": args.model, "api_mode": args.api_mode}
    result = run_replay(corpus, digest, route, levels, args.samples, call_target,
                        threshold=args.threshold, limit=args.limit, max_calls=args.max_calls,
                        max_cost=args.max_cost, prices=prices, now=now)
    written = [write_result(result, directory)]
    if args.repo_copy:
        written.append(write_result(result, REPO_COPY_DIR))
    print(f"{result['calls']} calls, {result['items']}/{result['items_planned']} items, "
          f"labelled {result['labelled']}, dropped {result['dropped']}, "
          f"non-monotonic {result['non_monotonic']}, distribution {result['label_distribution']}, "
          f"failures {result['failures']}", file=out)
    for level, state in result["level_status"].items():
        if state["rejected"]:
            print(f"level {level}: rejected by the provider {state['rejection_codes']}", file=out)
    if result["aborted"]:
        print(f"ABORTED: {result['aborted']} (partial result written)", file=out)
    for path in written:
        print(f"wrote {path.name} to {path.parent.name}/", file=out)
    return 1 if result["aborted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
