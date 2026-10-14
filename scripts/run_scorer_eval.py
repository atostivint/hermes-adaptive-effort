#!/usr/bin/env python3
"""Manual scorer evaluation on the synthetic corpus (billed; never run in CI).

Sends each corpus prompt to ONE selected scorer through the exact function the
middleware uses for named effort choices (``middleware._classify``), then writes a
dated JSON summary into the plugin data directory so the Desktop popup can show it.

Without ``--send`` this only validates the corpus and prints the planned call count;
it makes no network request. Settings come from ``middleware.DEFAULTS`` plus explicit
flags: ``~/.hermes/config.yaml`` is never read. Credentials resolve exactly as in the
plugin (``agent.secret_scope`` then environment) and are never printed or written.
Prompts are not written to the result (items carry corpus ids only).
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "scorer_eval_corpus.json"
RESULT_SCHEMA = "hermes-adaptive-effort.scorer-eval.v1"
PLUGIN_ID = "hermes-adaptive-effort"
PACKAGE = "hermes_plugin_adaptive_effort"
RESULT_DIR_NAME = "scorer-eval"
REPO_COPY_DIR = ROOT / "docs" / "validation" / "runs"
KNOWN_TAGS = frozenset({
    "short_followup", "fix_vs_explain", "trivial_vs_architectural", "multi_step", "ambiguous",
})


def load_plugin(name: str) -> Any:
    """Load one payload module inside the sibling-package layout the plugin loader builds."""
    if PACKAGE not in sys.modules:
        package = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(PACKAGE, None, is_package=True))
        package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")


def ensure_hermes_on_path() -> None:
    """Make ``agent`` importable (HERMES_SOURCE_ROOT) so effort clamping works."""
    try:
        import agent.reasoning_effort  # noqa: F401
        return
    except Exception:
        pass
    root = os.environ.get("HERMES_SOURCE_ROOT")
    if root and (Path(root) / "agent" / "reasoning_effort.py").is_file():
        sys.path.insert(0, root)


def load_corpus(path: Path = CORPUS_PATH) -> Tuple[Dict[str, Any], str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def validate_corpus(corpus: Any, levels: Sequence[str]) -> List[str]:
    """Return problems (empty when the corpus is well formed)."""
    problems: List[str] = []
    items = corpus.get("items") if isinstance(corpus, dict) else None
    if not isinstance(items, list) or not items:
        return ["items missing"]
    if not isinstance(corpus.get("version"), str) or not corpus["version"]:
        problems.append("version missing")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            problems.append("item is not an object")
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            problems.append(f"bad or duplicate id {ident!r}")
        seen.add(ident)
        if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
            problems.append(f"{ident}: empty prompt")
        if item.get("expected") not in levels:
            problems.append(f"{ident}: unknown expected level")
        tags = item.get("tags")
        if not isinstance(tags, list) or not tags or not set(tags) <= KNOWN_TAGS:
            problems.append(f"{ident}: bad tags")
    return problems


def clamp_expectation(effort_module: Any, expected: str,
                      choices: Sequence[str]) -> Optional[str]:
    return effort_module.map_named_choice(expected, tuple(choices))


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; ``None`` for no samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 1)


def scorer_model_name(settings: Dict[str, Any], scorers: Any) -> str:
    provider = settings["scorer_provider"]
    if provider == scorers.JEV:
        return str(settings.get("jev_model") or "")
    if provider == scorers.CLOUDFLARE:
        return str(settings.get("cloudflare_model") or "")
    configured = str(settings.get("scorer_model") or "")
    if provider == scorers.OPENAI_DECISION and not configured:
        return str(scorers.openai_decision_client.DEFAULT_MODEL)
    return configured


def run_evaluation(corpus: Dict[str, Any], corpus_sha256: str, settings: Dict[str, Any],
                   choices: Sequence[str], *, effort_module: Any, middleware: Any,
                   scorers: Any, limit: Optional[int] = None,
                   clock: Callable[[], float] = time.monotonic,
                   now: Optional[_datetime.datetime] = None) -> Dict[str, Any]:
    """Classify the corpus sequentially and build the result document."""
    choices = tuple(choices)
    items = list(corpus["items"])
    if limit is not None:
        items = items[:max(0, limit)]
    rows: List[Dict[str, Any]] = []
    matrix: Dict[str, Dict[str, int]] = {}
    failures: Dict[str, int] = {}
    timings: List[float] = []
    correct = 0
    for item in items:
        clamped = clamp_expectation(effort_module, item["expected"], choices)
        started = clock()
        answer, failure = middleware._classify(item["prompt"], settings, choices)
        elapsed_ms = max(0.0, (clock() - started) * 1000.0)
        timings.append(elapsed_ms)
        if not isinstance(answer, str) or answer not in choices:
            answer, failure = None, failure or "malformed_response"
        column = answer if answer is not None else "failed"
        matrix.setdefault(str(clamped), {}).setdefault(column, 0)
        matrix[str(clamped)][column] += 1
        if failure:
            failures[failure] = failures.get(failure, 0) + 1
        if answer is not None and answer == clamped:
            correct += 1
        rows.append({
            "id": item["id"], "expected": item["expected"], "expected_clamped": clamped,
            "answered": answer, "failure": failure, "ms": round(elapsed_ms, 1),
        })
    stamp = (now or _datetime.datetime.now(_datetime.timezone.utc)).astimezone(
        _datetime.timezone.utc)
    total = len(rows)
    return {
        "schema": RESULT_SCHEMA,
        "provider": settings["scorer_provider"],
        "scorer_model": scorer_model_name(settings, scorers),
        "endpoint": scorers.safe_endpoint_display(
            settings.get("custom_endpoint") or settings.get("endpoint")),
        "choices": list(choices),
        "corpus_version": corpus["version"],
        "corpus_sha256": corpus_sha256,
        "run_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": total,
        "answered": total - sum(failures.values()),
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else None,
        "p50_ms": percentile(timings, 50),
        "p95_ms": percentile(timings, 95),
        "failures": dict(sorted(failures.items())),
        "confusion": matrix,
        "results": rows,
    }


def result_filename(result: Dict[str, Any]) -> str:
    stamp = result["run_at"].replace("-", "").replace(":", "")
    provider = "".join(ch for ch in result["provider"] if ch.isalnum() or ch in "-_") or "scorer"
    return f"scorer-eval-{provider}-{stamp}.json"


def write_result(result: Dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / result_filename(result)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return target


def default_result_dir() -> Path:
    from hermes_cli.plugins_state import PluginState
    return PluginState(PLUGIN_ID).data_dir / RESULT_DIR_NAME


def build_settings(args: argparse.Namespace, middleware: Any, scorers: Any) -> Dict[str, Any]:
    settings = dict(middleware.DEFAULTS)
    settings.update({
        "scorer_provider": args.provider,
        "scorer_model": args.scorer_model or "",
        "jev_model": args.jev_model or middleware.DEFAULTS["jev_model"],
        "endpoint": args.endpoint or middleware.DEFAULTS["endpoint"],
        "cloudflare_account_id": args.cloudflare_account_id or "",
        "cloudflare_model": scorers.cloudflare_client.normalize_model_selector(
            args.cloudflare_model),
        "custom_endpoint": args.custom_endpoint or "",
        "custom_api_format": args.custom_api_format,
        "custom_auth": args.custom_auth,
        "classification_instructions": "",
        "use_target_model_context": False,
    })
    if args.instructions_file:
        text = Path(args.instructions_file).read_text(encoding="utf-8")
        settings["classification_instructions"] = middleware._rubric.normalize_classification_instructions(text)
    settings["timeout_s"] = (args.timeout_s if args.timeout_s and args.timeout_s > 0
                             else scorers.default_timeout_s(args.provider))
    return settings


def parse_choices(value: str, levels: Sequence[str]) -> Tuple[str, ...]:
    wanted = [part.strip().lower() for part in value.split(",") if part.strip()]
    unknown = [part for part in wanted if part not in levels]
    if unknown or not wanted:
        raise ValueError(f"--choices must be a non-empty subset of {', '.join(levels)}")
    return tuple(level for level in levels if level in wanted)


def make_parser(providers: Sequence[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", default="jev", choices=list(providers))
    parser.add_argument("--scorer-model")
    parser.add_argument("--jev-model")
    parser.add_argument("--endpoint")
    parser.add_argument("--cloudflare-account-id")
    parser.add_argument("--cloudflare-model", default="clef")
    parser.add_argument("--custom-endpoint")
    parser.add_argument("--custom-api-format", default="systemone")
    parser.add_argument("--custom-auth", default="none")
    parser.add_argument("--timeout-s", type=float, default=0.0,
                        help="per-call deadline; 0 uses the provider default")
    parser.add_argument("--choices", default=None,
                        help="comma-separated subset of the six-level scale (default: all)")
    parser.add_argument("--instructions-file",
                        help="optional classification guidance file (never echoed)")
    parser.add_argument("--limit", type=int, help="evaluate only the first N prompts")
    parser.add_argument("--send", action="store_true",
                        help="actually call the scorer (billed); without it nothing is sent")
    parser.add_argument("--out-dir", help="override the plugin data directory (tests/offline)")
    parser.add_argument("--repo-copy", action="store_true",
                        help="also write the result to docs/validation/runs/")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, out: Any = None,
         clock: Callable[[], float] = time.monotonic,
         now: Optional[_datetime.datetime] = None) -> int:
    out = out or sys.stdout
    ensure_hermes_on_path()
    effort_module = load_plugin("effort")
    middleware = load_plugin("middleware")
    scorers = load_plugin("scorers")
    levels = effort_module.CHOICE_LEVELS
    args = make_parser(scorers.PROVIDERS).parse_args(argv)
    try:
        choices = parse_choices(args.choices, levels) if args.choices else tuple(levels)
    except ValueError as exc:
        print(f"error: {exc}", file=out)
        return 2
    corpus, digest = load_corpus()
    problems = validate_corpus(corpus, levels)
    if problems:
        print("error: corpus invalid: " + "; ".join(problems[:5]), file=out)
        return 2
    if any(clamp_expectation(effort_module, level, choices) is None for level in levels):
        print("error: effort clamping unavailable (set HERMES_SOURCE_ROOT to a Hermes checkout)",
              file=out)
        return 2
    count = len(corpus["items"]) if args.limit is None else min(len(corpus["items"]),
                                                                 max(0, args.limit))
    print(f"corpus {corpus['version']} ({count} of {len(corpus['items'])} prompts), "
          f"provider {args.provider}, choices {','.join(choices)}", file=out)
    if not args.send:
        print(f"planned scorer calls: {count} (nothing sent; add --send to run, billed by the "
              "scorer provider)", file=out)
        return 0
    if args.out_dir:
        directory = Path(args.out_dir)
    else:
        try:
            directory = default_result_dir()
        except Exception:
            print("error: plugin data directory unavailable (Hermes not importable); "
                  "pass --out-dir", file=out)
            return 2
    settings = build_settings(args, middleware, scorers)
    result = run_evaluation(corpus, digest, settings, choices, effort_module=effort_module,
                            middleware=middleware, scorers=scorers, limit=args.limit,
                            clock=clock, now=now)
    written = [write_result(result, directory)]
    if args.repo_copy:
        written.append(write_result(result, REPO_COPY_DIR))
    print(f"accuracy {result['accuracy']} over {result['items']} items, "
          f"p50 {result['p50_ms']} ms, p95 {result['p95_ms']} ms, failures {result['failures']}",
          file=out)
    for path in written:
        print(f"wrote {path.name} to {path.parent.name}/", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
