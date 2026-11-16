# New OpenCode Go ids: which ones carry a reasoning-effort field — 2026-11-16

Endpoint `https://opencode.ai/zen/go/v1/chat/completions` (Chat Completions), one POST per
`(model, level)`, carrier `reasoning_effort` (top-level), stable `x-opencode-session`, a
fixed ~50-token system line and the one-line probe question, `max_tokens: 4`,
`temperature: 0`, no streaming, no redirect followed. Evidence:
[probe.jsonl](probe.jsonl), one redacted row per request. Reproduce with:

```powershell
python scripts/probe_opencode_go.py --catalog --dry-run `
  --model-levels "hy4-flash-free=minimal,low,medium,high,xhigh,max;hy4=low,medium,high,xhigh,max;..."
```

HY4Flash was probed first and reported before the rest of the run. Candidate ladders:
`minimal..max` (six) for `hy4-flash-free`, `low..max` (five) for every other id. No ladder
was inferred from a model name.

## Results

`200` = accepted (the gateway took the value). `403 access` = HTTP 403 "Model access is
disabled" — a per-model workspace access decision, not a credential failure. `429` = rate
limited. `400 max` = the gateway refused that one level and named the supported set.

| model | minimal | low | medium | high | xhigh | max | outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `hy4-flash-free` | 200 | 200 | 200 | 200 | 200 | 200 | usable |
| `hy4` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `hy4-preview` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `hy3` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `qwen4-max` | — | 200 | 200 | 200 | 200 | 200 | usable |
| `qwen3.8-flash` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `qwen3.8-max` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `qwen3.7-plus` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `kimi-k2.7-code` | — | 200 | 200 | 200 | 200 | 200 | usable |
| `kimi-k3.1-pro` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `mimo-v2.6-pro` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `minimax-m3` | — | 403 access | 403 access | 403 access | 403 access | 403 access | access disabled |
| `longcat-2.0` | — | 200 | 200 | 200 | 200 | 200 | usable |
| `longcat-2.5-free` | — | 429 | 429 | 429 | 429 | 429 | rate limited |
| `iceman-contributor-free` | — | 200 | 200 | 200 | 200 | 400 max | usable, ceiling `xhigh` |

Error codes, verbatim (workspace id redacted):

- 403: `Model access is disabled. Change model access at https://opencode.ai/console/<workspace>/models.`
- 429: `Rate limit exceeded. Please try again later.`
- `iceman-contributor-free` / `max`: `Upstream request failed: [invalid_request_error] Model 'iceman-contributor-free' does not support reasoning_effort='max'; supported values: low, medium, high, xhigh.`

## Verdict per model

- **`hy4-flash-free`** — carrier `reasoning_effort`, **accepted `minimal`, `low`, `medium`,
  `high`, `xhigh`, `max`** (6/6 live 200). Measured: yes.
- **`qwen4-max`** — carrier `reasoning_effort`, **accepted `low`, `medium`, `high`, `xhigh`,
  `max`** (5/5 live 200). `minimal` not probed. Measured: yes.
- **`kimi-k2.7-code`** — carrier `reasoning_effort`, **accepted `low`, `medium`, `high`,
  `xhigh`, `max`** (5/5 live 200). `minimal` not probed. Measured: yes.
- **`longcat-2.0`** — carrier `reasoning_effort`, **accepted `low`, `medium`, `high`, `xhigh`,
  `max`** (5/5 live 200). `minimal` not probed. Measured: yes.
- **`iceman-contributor-free`** — carrier `reasoning_effort`, **accepted `low`, `medium`,
  `high`, `xhigh`; `max` refused** with the gateway's own list. Measured: yes, ceiling `xhigh`.
- **`longcat-2.5-free`** — **no usable effort field observed**: every call returned 429
  (rate limited), including all five retries. The vocabulary is unknown, not empty; nothing
  is established either way.
- **`hy4`, `hy4-preview`, `hy3`, `qwen3.8-flash`, `qwen3.8-max`, `qwen3.7-plus`,
  `kimi-k3.1-pro`, `mimo-v2.6-pro`, `minimax-m3`** — **model unusable on this workspace**
  (HTTP 403, model access disabled) on every level. No effort evidence either way: the
  request was refused before the body was judged, so these ids are neither confirmed nor
  refuted, and they must not be registered from this run.

Under the rule "live 200 before any registration", the ids this run supports are
`hy4-flash-free`, `qwen4-max`, `kimi-k2.7-code`, `longcat-2.0` and
`iceman-contributor-free` (ceiling `xhigh`). Nothing was registered: this is measurement only.

## Limits

- One account, one workspace, one day; the 403s are an access decision of *this* workspace,
  not a property of the models.
- One probe per `(model, level)`. Acceptance is not intensity: a 200 says the gateway took
  the value, not that the level changes how much the model reasons.
- No cache proof and no cost measurement; the prefix is a fixed ~50-token system line, so
  nothing here says how these routes behave with a real conversation or how they bill.
- `minimal` was not probed on any id except `hy4-flash-free` (the brief fixed the ladder at
  `low..max` for the rest).
- `longcat-2.5-free` was rate limited on the first attempt and on the single allowed retry;
  the 429 shape is a hard limit for this account, not a level rejection.
- The `iceman-contributor-free` ceiling is the gateway's own statement, not an inference.
- The script's own first label for the `hy4` 403 was `auth` (it stopped the run); the
  classifier now separates "model access is disabled" from a credential failure, and the
  `hy4` cell was re-probed with the corrected label. That first call is not in the JSONL.

**Requests spent: 82 of the 85 ceiling** (6 for `hy4-flash-free`, 1 discarded `hy4` attempt
from the mislabelled first run, 70 for the 14 models, 5 allowed retries on
`longcat-2.5-free`). No credential failure, no 5xx, no request retried outside the one
allowed retry per call.
