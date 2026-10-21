# Effort replay

A manual, **billed** measurement of which reasoning effort a target model actually needs on a fixed set of synthetic tasks with a checkable outcome. It never runs in CI, the unit tests make no network call, and it changes no runtime behaviour. It produces evaluation data only.

The scorer evaluation ([SCORER_EVAL.md](SCORER_EVAL.md)) compares a scorer with hand-written expected levels. A hand label says what someone believes a task needs. The replay asks the target model instead: for each item it finds the cheapest effort level whose answers pass a mechanical check, so scorer choices and hand labels can be compared with something measured.

## What it is

- Corpus: `tests/fixtures/effort_replay_corpus.json` (schema `hermes-adaptive-effort.replay-corpus.v1`), 30 synthetic prompts, no real user text. Each item has the scorer-corpus fields (`id`, `prompt`, `expected` hand label, `tags`) plus a `check`:
  - `exact`: normalised string equality (trimmed, case-folded, wrapping quotes and a final full stop dropped).
  - `regex`: `re.search`, case-insensitive.
  - `numeric`: the last number in the answer, with an optional absolute tolerance.
  - `json_schema`: the first JSON value in the answer against a small stdlib validator (`type`, `enum`, `const`, `required`, `properties`, `additionalProperties`, `items`, `minimum`, `maximum`).
  - No check executes model output, and no LLM judge is used.
- Route: `opencode-go/deepseek-v4.1-flash` over `chat_completions` by default. The levels come from `effort.route_choice_levels` (`low`, `medium`, `high`, `max`). The request carries `thinking: {"type": "enabled"}` and top-level `reasoning_effort`, the pair the plugin writes for this `paired_effort` route.
- Runner: `scripts/run_effort_replay.py`. For each item it makes N calls (default and minimum 3) at each level, checks every answer, and records the cheapest level whose pass rate reaches `--threshold` (default 1.0, i.e. all N). Pure logic lives in `scripts/replay_checks.py` and `scripts/replay_labels.py`.
- Transport: the endpoint defaults to the OpenCode Go chat route (`--endpoint` overrides it). The key is read from the variable named by `--key-env` through `agent.secret_scope`, then the environment, and is never printed or written. Redirects are never followed and nothing is retried, so every attempt is billed once.

## Running it

Without `--send` it validates the corpus and prints the planned call count (items x levels x N). Nothing is sent.

```bash
export HERMES_SOURCE_ROOT=/path/to/hermes-agent
# dry run: 5 items x 4 levels x 3 samples = 60 planned calls, no network
.venv/bin/python scripts/run_effort_replay.py --limit 5

# live, billed: start small and cap it
.venv/bin/python scripts/run_effort_replay.py --send --limit 5 --max-calls 90 \
  --key-env <NAME_OF_THE_OPENCODE_GO_KEY_VARIABLE> --out-dir <scratch-dir>

# full run: 30 x 4 x 3 = 360 calls
.venv/bin/python scripts/run_effort_replay.py --send --max-calls 400 --repo-copy \
  --key-env <NAME_OF_THE_OPENCODE_GO_KEY_VARIABLE> \
  --max-cost 2.00 --price-in-per-m <usd> --price-out-per-m <usd>
```

`--send` requires `--max-calls` or `--max-cost`. `--max-cost` needs both prices (USD per million tokens, from your own plan) and stops once the estimate reaches the cap; a response without usage figures aborts a cost-guarded run. A run that aborts writes the complete items it finished and exits 1.

Flags: `--provider`, `--model`, `--api-mode`, `--levels`, `-n/--samples`, `--threshold`, `--limit`, `--corpus`, `--send`, `--max-calls`, `--max-cost`, `--price-in-per-m`, `--price-out-per-m`, `--endpoint`, `--key-env`, `--max-tokens`, `--timeout-s`, `--out-dir`, `--repo-copy`. Only the `opencode-go` / `chat_completions` transport exists.

Each run writes `effort-replay-<model>-<corpus sha8>-<UTC date>.json` (schema `hermes-adaptive-effort.effort-replay.v1`) into the profile's plugin data directory (`plugin-data/hermes-adaptive-effort/effort-replay/`), or `--out-dir`. `--repo-copy` also writes it to `docs/validation/runs/`. Review a result before committing it: it holds ids and numbers only, never prompts, answers, keys, hosts or endpoints.

### Comparing with hand labels and a scorer

Run the scorer evaluation on the same corpus, then compare offline:

```bash
.venv/bin/python scripts/run_scorer_eval.py --provider jev --send \
  --corpus tests/fixtures/effort_replay_corpus.json --choices low,medium,high,max
.venv/bin/python scripts/compare_replay.py <effort-replay.json> --scorer <scorer-eval.json> [--json summary.json]
```

The two results must carry the same `corpus_sha256`; otherwise the comparison refuses to join them.

## Reading a result

- `results[].pass_rate`: passes / N per level. `null` means the provider rejected that level (see below).
- `results[].label`: the cheapest accepted level that reaches the threshold. `dropped: true` means no level passed; the item has no label and is left out of every comparison.
- `non_monotonic`: a lower level passes while a higher one does not. It marks noise or a check that depends on luck; it is reported, not hidden.
- `floor`: the label is the lowest accepted level, so nothing cheaper was available to test.
- `tokens` / `reasoning_tokens`: mean completion tokens (reasoning tokens when the provider reports them, otherwise `null`) per level. Completion tokens are the figure used for cost.
- `level_status`: per level, accepted / rejected / failed attempts and the rejection codes. A provider HTTP 400 or 422 on the effort value is classified `rejected`, not as a failed check. After two rejections with no accepted response a level is declared rejected and not called again. This settles whether `medium` is accepted on this route: `model_profiles.json` lists `low/high/max` for the lab model, the route registry also lists `medium`.
- `failures`: other target errors by code (timeout, `http_429`, ...). They count as failed attempts for pass rates.
- The comparison report gives hand-vs-derived and scorer-vs-derived confusion matrices (rows are the derived label), `under`/`over` counts, and per policy (derived, hand, scorer, always-high, always-medium) the mean completion tokens and mean pass rate read from the replay table at the level each policy picks. Items whose picked level the provider rejected, or where the scorer failed, are counted separately and left out of the means.

## What it does not mean

- Synthetic, short, checkable tasks are not real agent steps. "Cheapest passing" ignores quality beyond the check.
- One model, one day, N=3 samples per level is a sample, not a benchmark. Pass/fail is stochastic; read the pass rates and `non_monotonic` before trusting a label.
- Token savings are completion tokens on this corpus. They are not a bill and not a claim about cache behaviour or latency.
- The default route and endpoint are the ones recorded in this repository's reviews; whether the key you supply is entitled to them is not checked until a live run. No live run is recorded in this repository for this tool yet, so the figures it will produce are unmeasured until one is committed and reviewed.
