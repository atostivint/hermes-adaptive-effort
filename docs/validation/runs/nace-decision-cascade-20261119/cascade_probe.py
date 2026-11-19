"""Offline comparison: one-shot effort choice vs a binary-search cascade (Nace scorer).

No runtime code is changed. This script only reads the repo's corpus and calls the
same Nace adapter the plugin uses.

Usage:
    .venv/Scripts/python cascade_probe.py --dry-run     # no network, no credential
    .venv/Scripts/python cascade_probe.py --run         # live calls, NACE_API_KEY from env

Outputs ``results.jsonl`` next to this file, one row per (item, strategy).
Prompt text and credentials are never written or printed.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
CORPUS = REPO / "tests" / "fixtures" / "scorer_eval_corpus.json"
RESULTS = HERE / "results.jsonl"

PLUGIN_PACKAGE = "hermes_plugin_adaptive_effort"

#: The eight corpus tags the stratified subset must cover, in a fixed order.
TAGS = (
    "ambiguous",
    "correction_or_replan",
    "fix_vs_explain",
    "mechanical_edit",
    "multi_step",
    "short_followup",
    "strict_format",
    "trivial_vs_architectural",
)
SUBSET_SIZE = 30
MAX_CASCADE_QUESTIONS = 4


def _load_plugin_module(stem: str) -> Any:
    """Import ``<repo>/<stem>.py`` the way Hermes loads the plugin payload."""
    if PLUGIN_PACKAGE not in sys.modules:
        spec = importlib.machinery.ModuleSpec(PLUGIN_PACKAGE, None, is_package=True)
        pkg = importlib.util.module_from_spec(spec)
        pkg.__path__ = [str(REPO)]  # type: ignore[attr-defined]
        sys.modules[PLUGIN_PACKAGE] = pkg
    full_name = f"{PLUGIN_PACKAGE}.{stem}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, str(REPO / f"{stem}.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _spread(pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Pick *count* items at evenly spaced positions in a corpus-ordered pool.

    The corpus is ordered by expected effort, so this spreads each tag across
    its difficulty range instead of taking only the first (easiest) items.
    """
    size = len(pool)
    if size <= count:
        return list(pool)
    chosen: list[int] = []
    for slot in range(count):
        index = min(size - 1, int((slot + 0.5) * size / count))
        while index in chosen and index + 1 < size:
            index += 1
        chosen.append(index)
    return [pool[index] for index in chosen]


def select_subset(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic stratified sample; the same 30 items for both strategies.

    Every tag in TAGS contributes up to four items, drawn evenly across that
    tag's difficulty range. A final round-robin trims the candidate pool to 30.
    """
    per_tag = 4
    candidates = {tag: _spread([item for item in items if tag in item["tags"]], per_tag)
                  for tag in TAGS}
    cursors = {tag: 0 for tag in TAGS}
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    while len(picked) < SUBSET_SIZE:
        progressed = False
        for tag in TAGS:
            if len(picked) >= SUBSET_SIZE:
                break
            pool = candidates[tag]
            while cursors[tag] < len(pool) and pool[cursors[tag]]["id"] in seen:
                cursors[tag] += 1
            if cursors[tag] < len(pool):
                item = pool[cursors[tag]]
                cursors[tag] += 1
                picked.append(item)
                seen.add(item["id"])
                progressed = True
        if not progressed:
            break
    corpus_order = {item["id"]: i for i, item in enumerate(items)}
    picked.sort(key=lambda item: corpus_order[item["id"]])
    return picked


def cascade_questions(level: str) -> dict[str, Any]:
    """One strict System One yes/no choice question for *level*."""
    instructions = (
        f"Answer yes if the task shown requires reasoning effort strictly greater than "
        f"{level} to produce a correct first reply; answer no otherwise."
    )
    return {"effort": {
        "type": "choice",
        "instructions": instructions,
        "criteria": {
            "yes": f"The task requires reasoning effort strictly greater than {level}.",
            "no": f"The task does not require reasoning effort strictly greater than {level}.",
        },
    }}


class StatusSpy:
    """Wrap the default transport to notice 401/402 without ever reading a secret."""

    def __init__(self) -> None:
        self.codes: list[Optional[int]] = []
        self.auth_fail = False

    def __call__(self, request: Any, timeout: float) -> Any:
        try:
            return nace_client_scorer_http.default_transport(request, timeout)
        except urllib.error.HTTPError as exc:
            code = getattr(exc, "code", None)
            self.codes.append(code)
            if code in (401, 402):
                self.auth_fail = True
            raise


# Bound after the plugin modules are loaded in main().
nace_client: Any = None
effort: Any = None
nace_client_scorer_http: Any = None


def ask_binary(client: Any, spy: StatusSpy, prompt: str, level: str,
               timeout: float) -> tuple[Optional[str], Optional[str], float, int]:
    """Return (answer, failure, latency_ms, invalid_answers) for one yes/no call."""
    body = {
        "model": client.model,
        "state": {"prompt": nace_client.truncate_prompt(prompt, client.max_prompt_chars)},
        "questions": cascade_questions(level),
    }
    request = nace_client_scorer_http.build_request(
        client.endpoint, body, key=client._key(),
        user_agent=nace_client_scorer_http.USER_AGENT,
        extra_headers={nace_client.CLIENT_HEADER: nace_client.CLIENT_ID},
    )
    started = time.monotonic()
    payload, failure = nace_client_scorer_http.send(request, spy, timeout)
    if failure:
        return None, failure, (time.monotonic() - started) * 1000, 0
    data, failure = nace_client_scorer_http.read_json(payload)
    if failure:
        return None, failure, (time.monotonic() - started) * 1000, 0
    try:
        answer = data["answers"]["effort"]
        value = answer["choice"]
        right_type = answer.get("type") == "choice"
    except (KeyError, TypeError, IndexError):
        return None, "malformed_response", (time.monotonic() - started) * 1000, 0
    if not right_type or not isinstance(value, str) or value not in ("yes", "no"):
        return None, "malformed_response", (time.monotonic() - started) * 1000, 1
    return value, None, (time.monotonic() - started) * 1000, 0


def run_one_shot(client: Any, prompt: str, ladder: tuple[str, ...],
                 timeout: float) -> dict[str, Any]:
    """The current pipeline: one named-choice question over the whole ladder."""
    started = time.monotonic()
    label, failure = client.classify_effort_detail(prompt, ladder)
    latency = (time.monotonic() - started) * 1000
    return {
        "label": label,
        "calls": 1,
        "failures": 1 if failure else 0,
        "latency_ms": round(latency, 1),
        "invalid_answers": 1 if failure == "malformed_response" else 0,
    }


def run_cascade(client: Any, spy: StatusSpy, prompt: str, ladder: tuple[str, ...],
                timeout: float) -> dict[str, Any]:
    """Binary search: one strict yes/no call per question, at most four calls."""
    low, high = 0, len(ladder) - 1
    calls = failures = invalid = 0
    latency = 0.0
    while low < high and calls < MAX_CASCADE_QUESTIONS:
        middle = (low + high) // 2
        answer, failure, elapsed, bad = ask_binary(
            client, spy, prompt, ladder[middle], timeout)
        calls += 1
        latency += elapsed
        invalid += bad
        if failure:
            failures += 1
            break
        if answer == "yes":  # effort > middle, so the answer is strictly above it
            low = middle + 1
        else:  # effort <= middle
            high = middle
    return {
        "label": None if failures else ladder[low],
        "calls": calls,
        "failures": failures,
        "latency_ms": round(latency, 1),
        "invalid_answers": invalid,
    }


def dry_run(subset: list[dict[str, Any]], ladder: tuple[str, ...]) -> None:
    counts = {tag: 0 for tag in TAGS}
    levels: dict[str, int] = {}
    for item in subset:
        for tag in item["tags"]:
            counts[tag] += 1
        levels[item["expected"]] = levels.get(item["expected"], 0) + 1
    print("subset_ids=" + " ".join(item["id"] for item in subset))
    print("tag_coverage=" + " ".join(f"{tag}:{counts[tag]}" for tag in TAGS))
    print("expected_levels=" + " ".join(f"{level}:{levels[level]}" for level in ladder
                                       if level in levels))
    print("ladder=" + " ".join(ladder))
    print(f"planned_calls: oneshot={len(subset)} cascade<={len(subset) * 3} "
          f"total<={len(subset) * 4} budget={len(subset) * 5}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="no network or credential")
    group.add_argument("--run", action="store_true", help="call Nace with NACE_API_KEY from env")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="per-call timeout; default 30 s for both strategies")
    args = parser.parse_args()

    global nace_client, effort, nace_client_scorer_http
    nace_client = _load_plugin_module("nace_client")
    effort = _load_plugin_module("effort")
    nace_client_scorer_http = _load_plugin_module("scorer_http")

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    items = corpus["items"]
    subset = select_subset(items)
    ladder = tuple(effort.CHOICE_LEVELS)

    if args.dry_run:
        dry_run(subset, ladder)
        return 0

    if not (os.environ.get("NACE_API_KEY") or "").strip():
        print("credential_missing")
        return 2

    spy = StatusSpy()
    client = nace_client.NaceClient(transport=spy, timeout=args.timeout)
    print(f"credential_present=True endpoint={client.endpoint} model={client.model} "
          f"timeout={client.timeout}")
    RESULTS.write_text("", encoding="utf-8")
    for item in subset:
        for strategy in ("oneshot", "cascade"):
            if strategy == "oneshot":
                detail = run_one_shot(client, item["prompt"], ladder, args.timeout)
            else:
                detail = run_cascade(client, spy, item["prompt"], ladder, args.timeout)
            row = {
                "item_id": item["id"],
                "tags": item["tags"],
                "expected": item["expected"],
                "strategy": strategy,
                **detail,
            }
            with RESULTS.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
            if spy.auth_fail:
                print(f"STOP: scorer returned 401/402 (codes={spy.codes}) after "
                      f"{item['id']}/{strategy}")
                return 3
    print(f"done rows={len(subset) * 2} observed_codes={sorted({c for c in spy.codes if c})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
