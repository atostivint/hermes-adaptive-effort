# Nace Drex Max — live validation (2026-11-19)

Purpose: prove that the existing `nace_model` setting can select the Drex Max ids through
the current adapter unchanged, and that the shared System One rubric parses their answers.
No runtime code was changed.

## Method and secret handling

- `NACE_API_KEY` was resolved from Bitwarden (`bws secret list --output json` → exact key
  `NACE_API_KEY` → `bws secret get <id> --output json`), assigned to `$env:NACE_API_KEY`
  for the probe subprocess only, and removed in a `finally` block. The value was never
  printed and never written to a file. Both probe runs reported `key_resolved=True`,
  `probe_exit=0` and `env_removed=True`. The Bitwarden secret id was `b468a92a…`; no key
  material appears in any artifact here.
- `GET https://console.nace.ai/v1/models` (status 200, ~300 ms) with the key.
- 12 live `POST /v1/systemone` calls through `nace_client.NaceClient(model=<id>)` using a
  short synthetic task and a 30 s deadline, with a 1.5 s inter-call delay. A capturing
  transport recorded the raw response body; only ids, answer values, response `model`,
  failure codes and latencies were written to the artifacts. No task text or key is stored.
- Question shapes, all from the plugin's own rubric and parsers:
  - `choice` — `rubric.questions_for(None, ("low", "medium", "high"))`, parsed by
    `rubric.systemone_choice`;
  - `score` — numeric question from `rubric.questions_for(None, None)`, parsed by
    `rubric.systemone_score`;
  - `continuation` — `rubric.continuation_questions()`, parsed by `rubric.systemone_noul`.

## Available ids (ids only, from `/v1/models`)

The catalog uses `name` (not `id`) as its identifier field. All four brief candidates are
present; active vs alias vs retired is recorded in `models.json`:

| Candidate id | Catalog status | `alias_for` |
| --- | --- | --- |
| `drex-max` | present, retired 2026-11-08 | `drex-max-v1.1` |
| `drex-max-v1.1` | active (released 2026-10-28) | null |
| `drex-max-finance` | active (released 2026-11-01) | null |
| `drex-latest` | moving alias | `drex-max-v1.1` |

`drex-v2.0` (the unchanged default) is also listed and active, `alias_for: null`.

## Live results

Final clean run (all HTTP 200, all parses valid). Latency is end-to-end around the adapter
call, not the provider's `evaluation_time_ms`.

| id asked | shape | raw response `model` | answer | failure | HTTP status | ms |
| --- | --- | --- | --- | --- | --- | --- |
| `drex-max` | choice | `drex-max-v1.1` | `low` | — | 200 | 500 |
| `drex-max` | score | `drex-max-v1.1` | 0.143 | — | 200 | 407 |
| `drex-max` | continuation | `drex-max-v1.1` | 0.411 | — | 200 | 437 |
| `drex-max-v1.1` | choice | `drex-max-v1.1` | `low` | — | 200 | 391 |
| `drex-max-v1.1` | score | `drex-max-v1.1` | 0.143 | — | 200 | 515 |
| `drex-max-v1.1` | continuation | `drex-max-v1.1` | 0.411 | — | 200 | 391 |
| `drex-max-finance` | choice | `drex-max-finance` | `low` | — | 200 | 500 |
| `drex-max-finance` | score | `drex-max-finance` | 0.081 | — | 200 | 422 |
| `drex-max-finance` | continuation | `drex-max-finance` | 0.455 | — | 200 | 453 |
| `drex-latest` | choice | `drex-max-v1.1` | `low` | — | 200 | 453 |
| `drex-latest` | score | `drex-max-v1.1` | 0.143 | — | 200 | 547 |
| `drex-latest` | continuation | `drex-max-v1.1` | 0.411 | — | 200 | 406 |

Every response carried exactly the documented top-level keys
`answers`, `evaluation_time_ms`, `model`, `usage`, `request_id`. Answer values are finite
and in contract: choice is one of the offered `low`/`medium`/`high`; score is in `0..2`;
continuation is a probability in `0..1`. `answers.effort` for choice carried
`type`/`choice`/`confidence`/`probabilities`; for score, `type`/`score`/`legend`/
`probabilities`/`confidence`; `answers.continuation` carried `type`/`noul`/`confidence`.

Successor-id behaviour is confirmed directly: asking `drex-max` (retired) and `drex-latest`
(alias) both returned raw `model: drex-max-v1.1`, while `drex-max-finance` returned its own
id. The adapter already ignores any mismatch between requested and reported model
(`nace_client.py` module docstring), so this needs no code change.

## Parsing diagnosis / why no adapter change

There was no parsing failure. Every answer that was received parsed with `failure: null`.
An earlier rapid run produced one `http_error` (HTTP 429) for `drex-max-v1.1` choice; it was
a transient rate limit under 12 back-to-back calls, not a response-shape problem. The
existing `scorer_retry` already retried other transient 429s to a valid 200 answer in that
same run, and a final 429 correctly fails open. Adding a delay removed the rate limit and
all 12 calls succeeded. This is documented, not worked around: no heuristic or
model-specific branch was added to `nace_client.py`.

The pass-through itself is already pinned by an existing unit test
(`tests/test_scorers.py::test_nace_defaults_to_pinned_drex_and_reports_endpoint_and_model`,
which builds with `nace_model="drex-latest"` and asserts `client.model`), so no new test was
needed.

## Gaps

- Availability reflects this key on 2026-11-19; it can change.
- No cost, answer-quality or long-state (>8k-token) subset behaviour was measured; the
  262,144-token shared whole-request budget and the >8k subset selection are taken from the
  catalog descriptions and the brief, not re-measured here.
- Max prices, the 529-on-evaluate-timeout path and 422-over-limit paths were not triggered
  in this run; only the documented 429 rate limit was observed.
- Answers come from one short synthetic task per shape; this proves shape compatibility,
  not scorer quality or equivalence with `drex-v2.0`.

## Artifacts

- `probe.py`, `probe.ps1` — reproducible probe (key resolved at run time, never stored)
- `models.json`, `models_raw.json` — catalog ids, alias/retired status, raw catalog body
- `live_results.jsonl`, `live_results.json` — per-call results (no task text, no key)
