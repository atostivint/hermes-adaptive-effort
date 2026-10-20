# Scorer evaluation

A manual, **billed** measurement of how one selected scorer answers the named-choice contract on a fixed set of synthetic prompts. It never runs in CI and the unit tests make no network call.

## What it is

- Corpus: `tests/fixtures/scorer_eval_corpus.json`, 86 prompts written for this file (no real user text), 11 each at `minimal`, `xhigh` and `max` and 17 to 18 each at `low`, `medium` and `high`, on the full scale `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. The weight is on boundary cases, tagged `short_followup`, `fix_vs_explain`, `trivial_vs_architectural`, `multi_step` and `ambiguous`, plus `correction_or_replan` (a request to redo, correct or change the plan), `strict_format` and `mechanical_edit` (technical wording with a strict output or a purely mechanical change, with high-expected controls that guard against under-rating). The `expected` level is a labelling judgement, not ground truth. The file carries a `version` (currently `2026-10-20`: the 20 added cases are labelled before any live run, so results from the earlier 66-item corpus are not comparable); each result records the corpus version and SHA-256, so results from different corpora must not be compared.
- Script: `scripts/run_scorer_eval.py` calls `middleware._classify(prompt, settings, choices)`, the same path the middleware uses: same adapters, credential lookup (`agent.secret_scope`, then environment), strict validation and fail-open reason codes. Calls are sequential and each is timed.
- Settings come from `middleware.DEFAULTS` plus explicit flags. `~/.hermes/config.yaml` is never read, and classification guidance stays empty unless `--instructions-file` is given.

## Running it

Without `--send` it only validates the corpus and prints the number of planned calls.

```bash
export HERMES_SOURCE_ROOT=/path/to/hermes-agent
.venv/bin/python scripts/run_scorer_eval.py --provider jev                 # dry run
.venv/bin/python scripts/run_scorer_eval.py --provider jev --send          # billed
.venv/bin/python scripts/run_scorer_eval.py --provider openrouter --scorer-model <decision-model-id> --send --limit 10
```

Flags: `--corpus PATH` (default: the scorer corpus; use `tests/fixtures/effort_replay_corpus.json` to compare with an [effort replay](EFFORT_REPLAY.md)), `--provider`, `--scorer-model`, `--jev-model`, `--endpoint`, `--cloudflare-account-id`, `--cloudflare-model`, `--custom-endpoint`, `--custom-api-format`, `--custom-auth`, `--timeout-s` (0 = provider default), `--choices`, `--instructions-file`, `--limit N`, `--out-dir`, `--repo-copy`.

`--choices` takes any subset of the six levels (default: all). Each expectation is clamped onto that subset with `effort.map_named_choice`, the clamp the plugin applies on a route; items record both `expected` and `expected_clamped`. Use `--choices low,medium,high` to evaluate a three-level route.

Each run writes `scorer-eval-<provider>-<UTC timestamp>.json` (schema `hermes-adaptive-effort.scorer-eval.v1`) into the profile's plugin data directory (`plugin-data/hermes-adaptive-effort/scorer-eval/`), where `GET /eval` and the Desktop popup read it. `--repo-copy` also writes it to `docs/validation/runs/`. Review a result before committing it: it holds ids and numbers only, never prompts, keys or credentialed URLs (endpoints are shown through `scorers.safe_endpoint_display`).

## Reading a result

- `accuracy`: answers equal to `expected_clamped`, divided by items. A failed call counts as wrong.
- `confusion`: expected (clamped) by answered, with a `failed` column for calls that produced no usable choice.
- `p50_ms`, `p95_ms`: nearest-rank latency over all calls, including failures and timeouts.
- `failures`: counts by reason code (`timeout`, `malformed_response`, `scorer_paused` and so on).

## What the numbers do not mean

- The prompts are synthetic and few. One run on one day is a sample, not a benchmark; a scorer, network or price can change tomorrow.
- Agreement with the corpus labels is not answer quality, task success, cost or savings. Nothing here was compared with a live A/B of reasoning effort.
- Latency includes your network path and the provider's load at that moment.
- Each run sends every selected prompt to the chosen provider and is billed by that provider. Use `--limit` first.
