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

## Recorded runs

### 2026-10-21: rubric wording campaign (issues #72 and #75)

Four rubric wordings were compared on corpus `2026-10-20` (SHA-256 `77340aa3...1406c`, 86 prompts), all on the same day with the same flags: `master`, the #72 sentence and `high` clause only (`72`), the #75 sentence only (`75`), and both (`72-75`). Gating scorer: Nace `drex-v2.0`, two repeats per arm. Jev and OpenAI Decisions ran once per arm and are reported, not gating. Raw results (ids and numbers only) are in `runs/rubric-campaign-20261021/`.

Nace, correct answers of 86 (two repeats) and the quantities the ship rule checks:

| Arm | Accuracy | Recall on `high`/`xhigh`/`max` (of 39) | `correction_or_replan` (of 10) | `strict_format` (of 6) | `mechanical_edit` (of 4) | p95 ms | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| master | 39, 38 | 5, 4 | 7, 7 | 4, 4 | 3, 3 | 391, 390 | 0 |
| #72 | 37, 38 | 6, 6 | 7, 7 | 4, 4 | 3, 3 | 391, 391 | 0 |
| #75 | 38, 37 | 5, 4 | 8, 8 | 4, 4 | 3, 3 | 407, 391 | 0 |
| #72+#75 | 36, 37 | 5, 6 | 7, 7 | 4, 4 | 3, 3 | 421, 406 | 0 |

- Noise: the two master repeats differ by 1 item in accuracy (4 of 86 answers changed), so the tolerance is 1 item.
- **#72: meets the ship rule, at the edge.** Mean accuracy is 1.0 below master (the tolerance), recall on the upper levels is 1.5 items above master, its new-case subset ties and p95 is equal. The gain is within the measured noise: this is "not worse", not "better".
- **#75: does not meet the rule.** Mean accuracy is also 1.0 below master, but p95 is 8.5 ms above master (p50 is equal), so "p95 not worse" is not met on a strict reading. The margin is small. The claim behind #75, that excess effort lowers correctness, is not measured here: this corpus measures agreement with labels only.
- **#72+#75: does not meet the rule** (mean accuracy 2.0 below master, p95 above master).
- Jev (one run each): 40, 41, 43 and 42 of 86 for `master`, #72, #75 and #72+#75. OpenAI Decisions (one run each): 18, 19, 19 and 17 of 86, close to chance for six levels (it never answered `xhigh` or `max` and labelled every `medium` item `low` on `master`). Neither can separate the wordings.

Not measured: the 0..2 score contract (unknown vocabularies, `probe`), dialogue-context cases, answer quality, cost or savings.

Run validity: a first pass of the Nace runs, fired back to back without a pause, returned `http_error` on 10 to 61 of 86 calls in 7 of 8 runs and was discarded; it is not committed. The failing calls were not random, so partial results could not be compared. The recorded Nace runs were repeated with a 75 s pause between runs and all 8 completed with no failures. The cause was not established (throttling is the likely one); a 5-call check showed the key and endpoint were fine. Jev and OpenAI runs were complete in the first pass.

The `endpoint` field in a result is the generic `endpoint` setting echoed by the script, not the URL of the provider that was called: Nace and OpenAI results show the Jev default. The adapters build their own endpoints, and the Nace answers differ from Jev's on 40 of 86 `master` prompts, so the runs reached different services.

## What the numbers do not mean

- The prompts are synthetic and few. One run on one day is a sample, not a benchmark; a scorer, network or price can change tomorrow.
- Agreement with the corpus labels is not answer quality, task success, cost or savings. Nothing here was compared with a live A/B of reasoning effort.
- Latency includes your network path and the provider's load at that moment.
- Each run sends every selected prompt to the chosen provider and is billed by that provider. Use `--limit` first.
