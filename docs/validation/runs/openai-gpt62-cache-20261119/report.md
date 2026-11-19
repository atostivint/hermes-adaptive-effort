# GPT-6.2 prompt cache under reasoning-effort changes — 2026-11-19

Question: **do OpenAI's documented GPT-6.2 caching changes affect the way this plugin
changes reasoning effort?** Measurement only — no runtime code, configuration or prompt
was changed. Four arms on `https://api.openai.com/v1/responses`, model
**`gpt-6.2-sol`** (fallback `gpt-6.2-astra` was not needed), one POST per call,
sequential, `max_output_tokens: 16`, no temperature, no tools, no
`previous_response_id`, no redirect. Fixed ~1,800-token synthetic prefix (7,762
characters of numbered filler, no user data, no real prompts); identical prefix in
every arm, only the documented control differs. `cached_tokens` / `cache_write_tokens`
/ `input_tokens` come from the provider's own response and were not cross-checked
against billing.

Script: `probe_gpt62_cache.py` (`--dry-run`, `--summarize`). Credential wrapper:
`run.ps1` — resolves `OPENAI_API_KEY` from Bitwarden Secrets Manager (1 entry), sets
it in the environment only for the probe subprocess and removes it in a `finally`
(`env_removed=True`); the value was never echoed and never written to any file.
Raw evidence: [cache.jsonl](cache.jsonl) (17 rows, all HTTP 200, 0 retries, 0 429/5xx,
17/45 request budget used).

## Measured tables

`diag` is `diagnostics_reason` (null = field absent). `cw` is `cache_write_tokens`.
The configuration-update count in arm C is from the plan (the JSONL stores the
boolean `used_configuration_update`, not the count).

### Arm A — constant effort, developer-message prefix (control)

| call | effort | top-level | config_update | input_tokens | cached_tokens | cw | diag | HTTP |
| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| 1 | low | low | no | 1774 | 0 | 1771 | — | 200 |
| 2 | low | low | no | 1774 | 1771 | 0 | — | 200 |
| 3 | low | low | no | 1774 | 1771 | 0 | — | 200 |
| 4 | low | low | no | 1774 | 1771 | 0 | — | 200 |

Control works: one cold write, then three full reads of the 1,771-token block.

### Arm B — top-level `reasoning.effort` changes, input unchanged

| call | effort | top-level | config_update | input_tokens | cached_tokens | cw | diag | HTTP |
| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| 1 | low | low | no | 1774 | 1771 | 0 | — | 200 |
| 2 | high | high | no | 1774 | **0** | **1771** | — | 200 |
| 3 | low | low | no | 1774 | 1771 | 0 | — | 200 |
| 4 | high | high | no | 1774 | 1771 | 0 | — | 200 |
| 5 | low | low | no | 1774 | 1771 | 0 | — | 200 |

The first `high` (call 2) read 0 and wrote the block; the second `high` (call 4) hit.
`input_tokens` stayed 1774 on every call, so the miss is not a longer prompt.

### Arm C — top-level effort fixed at `low`, `configuration_update(high)` appended

| call | effort | top-level | config_update | input_tokens | cached_tokens | cw | diag | HTTP |
| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| 1 | low | low | no | 1774 | 1771 | 0 | — | 200 |
| 2 | high | low | yes (1 item) | 1774 | 1771 | 0 | — | 200 |
| 3 | high | low | yes (2 items) | 1774 | 1771 | 0 | — | 200 |
| 4 | high | low | yes (3 items) | 1774 | 1771 | 0 | — | 200 |

Each new trailing item kept the earlier items byte-identical and in order. Every call
after the first still read 1,771 cached tokens; no call wrote the block.

### Arm D — filler moved to top-level `instructions`, constant effort

| call | effort | top-level | config_update | input_tokens | cached_tokens | cw | diag | HTTP |
| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| 1 | low | low | no | 1774 | 0 | 0 | — | 200 |
| 2 | low | low | no | 1774 | 0 | 0 | — | 200 |
| 3 | low | low | no | 1774 | 0 | 0 | — | 200 |
| 4 | low | low | no | 1774 | 0 | 0 | — | 200 |

Same 1,774 input tokens as arm A, but no call ever read or wrote a cache block.

## Verdict

1. **Does changing top-level `reasoning.effort` between calls reduce the cache read
   versus the constant-effort control?** **Yes, but only for the first use of a value.**
   The control (A) read 1,771 cached tokens on calls 2-4 and never wrote again. In B,
   the first `high` (call 2) read **0** cached and wrote **1,771**, while the `low`
   calls around it read 1,771; the second `high` (call 4) then hit 1,771. So the cost
   is one cold miss per newly seen value — the same "value partitions the prefix
   cache" shape seen in the separate `opencode-go-cache-20261114` run — not a
   persistent penalty on every change and not a full invalidation. Single observation
   per cell (n=1); call 1 of B was already warm from arm A, so the clean comparison is
   B call 2 against A's steady state and B calls 3/5.

2. **Does `configuration_update` preserve the read where the top-level change does
   not?** **Yes, in this run.** With top-level effort fixed at `low`, appending a
   `configuration_update(high)` item on calls 2-4 preserved 1,771 cached tokens on
   every one of those calls (`cw=0`). Where B's top-level change at call 2 read 0,
   C's equivalent trailing change read the full block. The documented
   cache-preserving pattern therefore holds on this model/account for the shape
   tested (append at the end, keep earlier items byte-identical). It is not evidence
   about changing effort *between turns* with a fresh, previously unseen value in a
   real conversation — C only ever moved to `high`.

3. **Does moving the fixed text to top-level `instructions` destroy cacheability on
   GPT-6.2 as documented?** **Yes.** With the identical filler in `instructions`
   instead of a leading `developer` message and effort held constant, arm D read 0
   cached and wrote 0 cache tokens on all four calls, while arm A (same text in a
   `developer` message) read 1,771 from call 2. `input_tokens` was 1,774 in both arms,
   so the text was billed as input either way — only the `developer`-message placement
   formed a breakpoint. This matches the documented "appended after `input`, billed
   uncached, not eligible for breakpoints" behaviour.

4. **Can `prompt_cache_diagnostics` distinguish an effort-caused miss from an
   unrelated one, and what reason values appeared?** **Not in this run.** No response
   contained `prompt_cache_diagnostics`: `diagnostics_reason` and `diagnostics_full`
   were null in all 17 rows. **No reason values appeared** — the documented example
   `tools_changed` did not occur (no tools were sent). The probe captures the object
   verbatim when present, but it never was, so the B call-2 miss had to be attributed
   from counters alone (`cached_tokens=0` together with `cache_write_tokens=1771`
   on the only novel-value call). The field cannot currently be relied on to separate
   an effort miss from an unrelated one on this model/route.

### Implication for the plugin (measurement only)

The plugin's current shape — changing the request's top-level effort between turns —
is the one that produced the cold miss in arm B (once per newly seen value). Keeping a
fixed top-level effort and appending a trailing `configuration_update` item preserved
the read in arm C. This report does **not** change runtime code; it records the
observation for the operator to decide whether a carrier change is warranted.

## Limits

One account, one day, synthetic prefix, one probe per call, no quality or cost
measurement. Also: one model (`gpt-6.2-sol`), one endpoint, n=1 per cell (no
repetition), all four arms in one continuous session (no `previous_response_id`,
provider cache shared across arms), counters are the provider's own and were not
checked against billing or latency, ~1-token counter granularity (1,771 of 1,774
input tokens cached), no tools/temperature/streaming, and no observation of
`prompt_cache_diagnostics`. Nothing here measures reasoning intensity, answer quality
or billed cost.
