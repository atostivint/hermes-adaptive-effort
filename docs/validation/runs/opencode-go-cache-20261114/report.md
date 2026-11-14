# OpenCode Go prefix cache under effort changes — 2026-11-14

Question: **does changing `reasoning_effort` cost the provider's prefix cache on the OpenCode Go
gateway, and why?** Two runs answer it: a first pass (59 requests) that tested the shape of the
misses, and a controlled matrix (100 requests) that tested value, size and commit latency
separately. The matrix supersedes the first pass's conclusion; both are kept below, and the
reconciliation is stated explicitly in *Reconciling the two runs*.

Endpoint `https://opencode.ai/zen/go/v1/chat/completions`, model **`deepseek-v4.1-flash`**
(the bare slug — the task note's `deepseek/deepseek-v4.1-flash` is rejected with HTTP 400, see
[rejected-model-id.jsonl](rejected-model-id.jsonl)), fixed synthetic prefixes with no user data,
`max_tokens: 4`, `temperature: 0`, one POST per call, sequential, no streaming, no redirect
followed. `cached_tokens` is the gateway's own `usage.prompt_tokens_details.cached_tokens`
(falling back to `usage.cached_tokens`, then `usage.prompt_cache_hit_tokens`).

Script: `python scripts/probe_opencode_go_cache.py` (first pass, `--summarize` for tables),
`--matrix` for the second run (`--dry-run`, `--summarize-matrix`).
Raw evidence: [cache.jsonl](cache.jsonl) (40 rows), [cache-paired.jsonl](cache-paired.jsonl)
(5 rows), [confounded-run1.jsonl](confounded-run1.jsonl) (13 rows),
[cache-matrix.jsonl](cache-matrix.jsonl) (100 rows).

## Verdict

- **Effort value partitions the prefix cache.** The first call at a value the session has not
  used misses; the next call at that value hits. Small prefix (`low` → `max`): the first `max`
  missed in **3 of 3** repetitions (`0, 0, 0`) and every later `max` read the full 2,048-token
  block. Large prefix: the `low` → `max` shape missed in **3 of 3** runs (cell `F` plus the three
  delayed `I` runs at 0 s, 10 s and 30 s).
- **Prefix size sets the cost of that miss.** The same comparison loses a 2,048-token block at
  ~2.1k prompt tokens and 7,552 tokens at ~7.6k. At the large prefix the change missed *every*
  time it was tested and the prefix did not recover within one call (`F` needed three misses
  before a full hit). Extrapolation to production-sized conversations is **not measured**.
- **An asynchronous commit is refuted as the cause.** With the second call delayed, `low → max`
  read `0` cached tokens at 0 s, 10 s and 30 s, while the `low → low` control hit at every delay
  (and at 10 s converted a warm-up miss into a full 7,552-token hit). The entry the next call
  needs does not appear with time when the value changed; the same delay does help when it did
  not change.
- **Session identity is real but incomplete.** Changing nothing but `x-opencode-session` produced
  a miss on the first call of the new session and a hit on the next (first pass, `D`). In the
  matrix the new-session call missed in **2 of 3** small-prefix repetitions and **hit** in the
  single large-prefix run — so the session header takes part in the match, but it does not
  explain every miss on its own.
- **`none` is not a clean same-prefix value.** It renders **25 fewer prompt tokens** at both
  sizes (2,058 vs 2,083; 7,568 vs 7,593), so its cross-hits at 2,048 do not refute the partition:
  the gateway builds a different prompt for it.

## Setup

Two prefixes, both fixed, both measured:

| cell | characters | prompt tokens (`low`/`max`) | prompt tokens (`none`) |
| --- | ---: | ---: | ---: |
| `small` | 9,032 | 2,083 | 2,058 |
| `large` | 33,352 | 7,593 | 7,568 |

The gateway reports cache in 2,048-token blocks, so a "full hit" is 2,048 of 2,083 or 7,552 of
7,593 prompt tokens, never 100%.

## Run 1 — the shape of the misses (59 requests)

Conditions `A`–`F` on the small prefix. Values are `cached_tokens` in call order.

| condition | body | cached_tokens | reading |
| --- | --- | --- | --- |
| `A` | `low` ×8, one session | 0,2048,2048,2048,2048,2048,2048,2048 | control: the cache works, 7 hits after the cold call |
| `B` | `low,medium` ×4, fresh session | 0,**0**,2048,2048,2048,2048,2048,2048 | the first `medium` missed, every later one hit |
| `C` | `low,medium,low,medium,low`, fresh session | 0,**2048**,2048,2048,2048 | here the first `medium` **hit** |
| `C` paired | same, plus `thinking:{"type":"enabled"}` | 0,0,0,0,2048 | four misses, then a hit on a *change* call |
| `D` | `low` ×4, new `x-opencode-session`, `low` ×3 | 2048,2048,2048,2048,**0**,2048,2048 | identical body, only the session changed: miss then hit |
| `E` | `low` ×4, `max_tokens` 64, `low` | 2048 ×6 | an unrelated body field is not in the key |
| `F` | `low` ×4, `temperature` 0.2, `low` | 2048 ×6 | a sampling parameter is not in the key |

Two facts from this run survive into the matrix: the session header matters (`D`), and `B` vs `C`
disagree about whether the first `medium` misses — which is why run 1 alone could not settle the
question (see *Reconciling the two runs*).

## Run 2 — value, size, commit latency (100 requests)

### Arm 1 — small prefix, three repetitions, every cell on a fresh session

| cell | body | rep 1 | rep 2 | rep 3 | hits |
| --- | --- | --- | --- | --- | ---: |
| A | `low` ×6 | 0,2048,2048,2048,2048,2048 | 0,2048,2048,2048,2048,2048 | 0,2048,2048,2048,2048,2048 | 15/18 |
| B | `low,max` ×3 | 0,0,2048,2048,2048,2048 | 2048,0,2048,2048,2048,2048 | 0,0,0,2048,2048,2048 | 12/18 |
| C | `none,low` ×3 | 0,0,0,2048,0,0 | 0,0,2048,2048,2048,2048 | 0,2048,0,2048,2048,2048 | 9/18 |
| D | `low`×3, new session, `low`×2 | 2048,2048,2048,**0**,2048,2048 | 0,2048,2048,**0**,2048,2048 | 0,2048,2048,2048,2048,2048 | 14/18 |

`B` call 2 is the first `max` of that session: `0` in all three repetitions. Cell `C` is noisy by
construction — `none` renders a different prompt (25 tokens shorter), so it can only ever share
the 2,048-token block, and its cross-hits are the main source of variance here.

### Arm 2 — large prefix, one repetition per cell

| cell | body | cached_tokens | hits |
| --- | --- | --- | ---: |
| E | `low` ×4 | 0,7552,7552,7552 | 3/4 |
| F | `low,max,low,max` | 0,**0**,0,7552 | 1/4 |
| G | `none,low,none,low` | 0,0,0,7552 | 1/4 |
| H | `low`×2, new session, `low`×2 | 2048,7552,**7552**,7552 | 4/4 |

### Arm 3 — large prefix, second call delayed (`I` change vs `J` control)

| delay | I `low → max` | J `low → low` |
| ---: | --- | --- |
| 0 s | 7552, **0** | 7552, 7552 |
| 10 s | 2048, **0** | 0, 7552 |
| 30 s | 2048, **0** | 2048, 7552 |

`I` call 1 read the full 7,552 block and the immediately following `max` read `0` with no delay
involved: the miss belongs to the change, not to the request after a miss.

## Reconciling the two runs

Run 1's first pass read the post-change misses as *session warm-up* and concluded the effort value
was not in the key. That conclusion does not survive the matrix, and the reason is visible in the
data rather than in a mechanism:

- In the confounded first pass the same session covered `A` → `B` → `C` → `D`, so `B` had already
  sent `medium` before `C` sent it again. `C`'s "first use" was a re-use (preserved in
  [confounded-run1.jsonl](confounded-run1.jsonl)).
- Re-measured on fresh sessions, run 1 still disagrees with itself: `B` missed the first `medium`
  and `C` hit it (n=1 each). The matrix resolves that pair-size ambiguity only partly: it tested
  `low`/`max` (3/3 first-use misses at both sizes) and `none`/`low`, not `low`/`medium` again.
  So the honest statement is narrower than "value is the key": **`low` → `max` partitions
  reliably; `low` → `medium` did not partition consistently at ~2.1k tokens (one miss, one hit,
  n=1 each)**.
- Residual noise is real and is not explained here: `C` rep 1 shows a hit at `low` (call 4)
  followed by a miss at `low` (call 6) in the same session, which a strict per-value entry would
  forbid. At n=3 per cell this is not separable from cache-node routing. That is why the verdict
  says "partitions", not "is keyed by".
- The `none` prompt is 25 tokens shorter than the `low`/`max` prompt, which is itself a finding:
  the value reaches the rendered prompt text, not only a control field. It also means `none` rows
  are not byte-comparable with the other cells.

## What the plugin should assume

- **Do not treat the effort value as free.** On these exact routes a change to a value the session
  has not used costs one miss of the largest block the gateway reports, and at 7.6k tokens that
  loss was the whole prefix and did not always recover within one call.
- **The current pin stays, and it is a pin on unproven cost, not a verdict on the provider.**
  The plugin changes effort *between turns*, which is exactly the shape that missed in every
  controlled cell, and a real conversation's prefix is larger than this probe's. Nothing here
  shows a way to change effort without paying that miss; the delay arms remove the cheap
  explanation (a commit race) that would have made the pin unnecessary.
- **A miss right after a change must still not be read as proof by the route trial.** The
  inference `.route_trial` draws from provider counters (`observed_cache_loss`) remains a
  heuristic: at 2.1k tokens `low` → `medium` missed once and hit once, and cell `C` rep 1 has a
  same-value miss. The trial's baseline check reduces but does not remove that.
- **Pin the session id** if a route is ever tiered as cache-safe: the header is part of what a hit
  matches on (`D`, `H`).

## Limits, corrections and budget

- **n.** 3 repetitions per cell at the small prefix (arm 1), **1** at the large prefix (arm 2),
  1 per delay cell (arm 3). Only one model, one account, one machine, one day, one region.
- **No cache-node header** was returned, so node-level differences cannot be separated from the
  effect; this is the most likely source of the residual noise above.
- **Counters are the provider's own.** `cached_tokens` and `prompt_tokens` come from the
  response; nothing here was cross-checked against billing or latency. Block granularity is 2,048
  tokens, so anything below 64 tokens is invisible.
- **One model, one route family.** `deepseek-v4.1-flash` on `chat_completions` only: the other Go
  chat routes, the Go `codex_responses` routes and OpenCode Zen were not probed.
- **Prefixes are synthetic and fixed** (9,032 and 33,352 characters of filler); no real
  conversation, no tools, no streaming, `max_tokens: 4`. Nothing here measures whether a level
  changes reasoning intensity, answer quality, or billed cost beyond the prefix counters.
- **Model id.** The task note's prefixed `deepseek/deepseek-v4.1-flash` is rejected (HTTP 400,
  [rejected-model-id.jsonl](rejected-model-id.jsonl)); the bare slug was used throughout.
- **Confounded first pass.** Run 1's first full sequence reused one session across `A` → `D`; the
  affected rows are in [confounded-run1.jsonl](confounded-run1.jsonl) and were re-measured on
  fresh sessions. Their pattern (four consecutive misses in `B` on a *warm* session) is itself
  why a pure effort rule was rejected at the time.
- **Budget.** Run 1: 59 requests against a 40-request ceiling (1 rejected model-id attempt, 40 in
  the first pass, 13 to re-measure `B`/`C`, 5 for the paired-carrier check). Run 2: 100 requests
  against a 110 ceiling. No 429, no 5xx, no retry, nothing run concurrently.
