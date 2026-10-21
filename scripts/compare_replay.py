#!/usr/bin/env python3
"""Offline comparison of an effort replay result with hand labels and a scorer evaluation.

Reads a ``hermes-adaptive-effort.effort-replay.v1`` result and, optionally, a
``hermes-adaptive-effort.scorer-eval.v1`` result produced on the SAME replay corpus
(``run_scorer_eval.py --corpus tests/fixtures/effort_replay_corpus.json``), joined by item id.
It prints confusion matrices (derived label vs hand label, derived label vs scorer choice) and,
per policy, the mean completion tokens and pass rate read from the replay table at the level
that policy picks. No network, no prompts: ids and numbers only.

Read the figures as "synthetic tasks, one model, N samples per level", not as a benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPLAY_SCHEMA = "hermes-adaptive-effort.effort-replay.v1"
SCORER_SCHEMA = "hermes-adaptive-effort.scorer-eval.v1"


def confusion(pairs: Sequence[tuple], rows: Sequence[str], columns: Sequence[str]
              ) -> Dict[str, Dict[str, int]]:
    """``{reference: {answer: count}}`` for ``(reference, answer)`` pairs."""
    matrix = {row: {col: 0 for col in columns} for row in rows}
    for reference, answer in pairs:
        matrix.setdefault(reference, {col: 0 for col in columns})
        matrix[reference][answer] = matrix[reference].get(answer, 0) + 1
    return matrix


def direction(reference: str, answer: str, levels: Sequence[str]) -> str:
    a, b = levels.index(reference), levels.index(answer)
    return "exact" if a == b else ("under" if b < a else "over")


def policy_cost(policy: Dict[str, Optional[str]], rows: Dict[str, Dict[str, Any]],
                levels: Sequence[str]) -> Dict[str, Any]:
    """Mean tokens and pass rate of a ``{item_id: level}`` policy over the labelled items.

    Items whose picked level is missing (scorer failure), was rejected by the provider or has
    no token figure are counted separately and left out of the means.
    """
    tokens: List[float] = []
    rates: List[float] = []
    missing = rejected = 0
    for ident, row in rows.items():
        level = policy.get(ident)
        if level not in levels:
            missing += 1
            continue
        rate = row["pass_rate"].get(level)
        if rate is None:
            rejected += 1
            continue
        rates.append(rate)
        if row["tokens"].get(level) is not None:
            tokens.append(row["tokens"][level])
    return {
        "items": len(rates),
        "missing": missing,
        "rejected": rejected,
        "mean_pass_rate": round(sum(rates) / len(rates), 4) if rates else None,
        "mean_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
    }


def compare(replay: Dict[str, Any], scorer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the comparison document; raises ``ValueError`` on mismatched inputs."""
    if replay.get("schema") != REPLAY_SCHEMA:
        raise ValueError("not an effort replay result")
    levels = list(replay["levels"])
    labelled = {row["id"]: row for row in replay["results"] if row.get("label")}
    summary: Dict[str, Any] = {
        "replay": {"target": replay["target"], "corpus_version": replay["corpus_version"],
                   "samples_per_level": replay["samples_per_level"],
                   "items": replay["items"], "labelled": len(labelled),
                   "dropped": replay["dropped"], "non_monotonic": replay["non_monotonic"]},
        "levels": levels,
        "caveat": "synthetic tasks, one model, N samples per level",
    }
    derived = {ident: row["label"] for ident, row in labelled.items()}
    hand = {ident: row["expected"] for ident, row in labelled.items()}
    summary["hand_vs_derived"] = _agreement(derived, hand, levels)
    policies: Dict[str, Dict[str, Optional[str]]] = {
        "derived": dict(derived), "hand": dict(hand),
        "always_high": {i: "high" for i in labelled},
        "always_medium": {i: "medium" for i in labelled},
    }
    if scorer is not None:
        if scorer.get("schema") != SCORER_SCHEMA:
            raise ValueError("not a scorer evaluation result")
        if scorer.get("corpus_sha256") != replay.get("corpus_sha256"):
            raise ValueError("corpus mismatch: run both on the same corpus file")
        answers = {r["id"]: r.get("answered") for r in scorer["results"]}
        joined = {i: answers[i] for i in labelled if i in answers}
        summary["scorer"] = {"provider": scorer.get("provider"),
                             "model": scorer.get("scorer_model"),
                             "joined": len(joined), "not_in_scorer_result": len(labelled) - len(joined),
                             "failed": sum(1 for a in joined.values() if a not in levels)}
        summary["scorer_vs_derived"] = _agreement(
            {i: derived[i] for i in joined}, joined, levels)
        policies["scorer"] = {i: joined.get(i) for i in labelled}
    summary["policies"] = {name: policy_cost(pol, labelled, levels)
                           for name, pol in policies.items()}
    return summary


def _agreement(reference: Dict[str, str], answers: Dict[str, Optional[str]],
               levels: Sequence[str]) -> Dict[str, Any]:
    pairs = [(reference[i], a) for i, a in answers.items() if a in levels]
    counts = {"exact": 0, "under": 0, "over": 0}
    for ref, answer in pairs:
        counts[direction(ref, answer, levels)] += 1
    total = len(pairs)
    return {"items": total, "agreement": round(counts["exact"] / total, 4) if total else None,
            **counts, "confusion": confusion(pairs, levels, levels)}


def render(summary: Dict[str, Any]) -> str:
    levels = summary["levels"]
    lines = [f"Effort replay comparison ({summary['caveat']})",
             f"target {summary['replay']['target']['provider']}/"
             f"{summary['replay']['target']['model']}, corpus {summary['replay']['corpus_version']}, "
             f"{summary['replay']['labelled']} labelled of {summary['replay']['items']} items "
             f"({summary['replay']['dropped']} dropped, "
             f"{summary['replay']['non_monotonic']} non-monotonic)", ""]
    for key, title in (("hand_vs_derived", "hand label (columns) vs derived label (rows)"),
                       ("scorer_vs_derived", "scorer choice (columns) vs derived label (rows)")):
        block = summary.get(key)
        if not block:
            continue
        lines.append(f"{title}: agreement {block['agreement']} over {block['items']} items, "
                     f"under {block['under']}, over {block['over']}")
        lines.append("derived\\answer  " + "  ".join(f"{lv:>6}" for lv in levels))
        for row in levels:
            lines.append(f"{row:<14}  " + "  ".join(f"{block['confusion'][row].get(col, 0):>6}"
                                                    for col in levels))
        lines.append("")
    lines.append("policy          items  mean_pass_rate  mean_tokens  missing  rejected")
    for name, stat in summary["policies"].items():
        lines.append(f"{name:<14}  {stat['items']:>5}  {str(stat['mean_pass_rate']):>14}  "
                     f"{str(stat['mean_tokens']):>11}  {stat['missing']:>7}  {stat['rejected']:>8}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, out: Any = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("replay", help="effort-replay result JSON")
    parser.add_argument("--scorer", help="scorer-eval result JSON from the same corpus")
    parser.add_argument("--json", dest="json_out", help="also write the summary as JSON here")
    args = parser.parse_args(argv)
    try:
        replay = json.loads(Path(args.replay).read_text(encoding="utf-8"))
        scorer = (json.loads(Path(args.scorer).read_text(encoding="utf-8"))
                  if args.scorer else None)
        summary = compare(replay, scorer)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=out)
        return 2
    out.write(render(summary))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
