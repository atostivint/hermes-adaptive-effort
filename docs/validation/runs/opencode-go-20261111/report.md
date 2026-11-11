# OpenCode Go effort-vocabulary measurement — 2026-11-11

Endpoint: `https://opencode.ai/zen/go/v1` (provider `opencode-go`), one POST per
`(model, level)`, no redirect followed, no streaming. Evidence: [probe.jsonl](probe.jsonl),
one redacted JSON line per request (status, error code, latency; no prompt, no key, no
response body). Script: [probe_opencode_go.py](probe_opencode_go.py).

## What a 200 proves, and what it does not

A HTTP 200 proves **acceptance**: the gateway took this value for this exact model and did
not reject the request. It does **not** prove that the level changes the amount of
reasoning the model performs. The probe sent one minimal prompt per level and never
compared reasoning-token counts, latency over a controlled prompt, answer quality, cache
reads or cost. Intensity, cache behaviour, cost and answer quality are **unmeasured**.
This document is therefore a vocabulary measurement, not a capability or quality
benchmark.

## Measured matrix

Every level below returned HTTP 200 on the first attempt. No level was retried.

| Model | API mode | Carrier | Accepted levels | Registry outcome |
| --- | --- | --- | --- | --- |
| `minimax-m3.1-flash` | `chat_completions` | `reasoning_effort` | `low`, `medium`, `high`, `xhigh`, `max` | Registered 2026-11-11 |
| `step-5-preview` | `chat_completions` | `reasoning_effort` | `low`, `medium`, `high` | Registered 2026-11-11 |
| `glm-5.3-flash` | `chat_completions` | `reasoning_effort` | `low`, `high`, `max` | Registered 2026-11-11 |
| `deepseek-v4.1-flash` | `chat_completions` | `paired_effort` | `low`, `medium`, `high`, `max` | Already registered; confirmed unchanged |
| `deepseek-v4-pro` | `chat_completions` | `paired_effort` | `low`, `high`, `max` | Already registered; confirmed unchanged |
| `grok-4.7` | `codex_responses` | `reasoning` | `low`, `medium`, `high`, `xhigh` | Already registered; confirmed unchanged |
| `muse-spark-1.3-contributor` | `codex_responses` | `reasoning` | `minimal`, `low`, `medium`, `high`, `xhigh` | Already registered; confirmed unchanged (replay) |
| `beam-free` | `chat_completions` | `reasoning_effort` | none — HTTP 429 at every level | Stays unregistered |

`paired_effort` is the registry's name for the paired control the OpenCode Go DeepSeek
routes need: top-level `thinking: {"type": "enabled"}` plus `reasoning_effort`. The probe
sent both, as the route table declares.

No probe ever sent `none`. Nothing here opens the `allow_none` gate: no measured route
declares `none`, so the named-choice list stays `none`-free.

## The two cases not measured in the first pass, replayed

Both were re-run on 2026-11-11 against the same endpoint, with the credential read from
the environment and never printed.

### `muse-spark-1.3-contributor` (was HTTP 400, now accepted)

The first pass returned HTTP 400 `max_output_tokens must be >= 16` at every level. That was
a **probe-script defect**, not a level rejection: the Responses body asked for
`max_output_tokens: 8`. The script now sends `max_output_tokens: 16` (the Responses floor)
and the route uses the Responses API mode with the `reasoning` carrier.

| Level | HTTP | Verdict |
| --- | ---: | --- |
| `minimal` | 200 | accepted |
| `low` | 200 | accepted |
| `medium` | 200 | accepted |
| `high` | 200 | accepted |
| `xhigh` | 200 | accepted |

This confirms the Muse Contributor tier already in the registry (`minimal` through `xhigh`,
no `max`). The Muse table was not changed.

### `beam-free` (HTTP 429 again)

| Level | HTTP | Error code |
| --- | ---: | --- |
| `low` | 429 | `Rate limit exceeded. Please try again later.` |
| `medium` | 429 | `Rate limit exceeded. Please try again later.` |
| `high` | 429 | `Rate limit exceeded. Please try again later.` |
| `xhigh` | 429 | `Rate limit exceeded. Please try again later.` |
| `max` | 429 | `Rate limit exceeded. Please try again later.` |

Still rate-limited, so `beam-free` stays **unregistered**: no level has ever been accepted,
and a free-tier quota block is not evidence about the effort vocabulary.

## `deepseek-v4.1-flash`: catalog versus registry

The OpenCode CLI model catalog lists `low/high/max` for `deepseek-v4.1-flash`. The registry
has declared `low/medium/high/max` since before this run. The probe answered HTTP 200 at
`medium`, so **the probe is the deciding evidence** and the registry is right: the catalog
understates the route's vocabulary. The four-level list is pinned by test so a later
catalog refresh cannot silently narrow it.

Two other already-registered routes were confirmed rather than changed: `deepseek-v4-pro`
at `low/high/max` and `grok-4.7` at `low/medium/high/xhigh` on `codex_responses`.

## Reproduction

```powershell
$env:OPENCODE_GO_API_KEY = ...   # never printed; from the operator's secret store
.\.venv\Scripts\python.exe scripts\probe_opencode_go.py --models muse-spark-1.3-contributor
.\.venv\Scripts\python.exe scripts\probe_opencode_go.py --models beam-free
Remove-Item Env:\OPENCODE_GO_API_KEY
```

`--dry-run` prints the planned matrix without sending anything. The script appends one
redacted line per request to `probe.jsonl`; it never writes the prompt, the key or the
response body.

## Gaps

- Intensity per level: unmeasured for every route in this table (no reasoning-token,
  quality or cost comparison).
- Cache behaviour on an effort change: unmeasured for the three newly registered routes,
  so they stay non-dynamic and are not trialled.
- `beam-free`: no level accepted as of 2026-11-11 (429 on all five).
- Other API modes for the three new IDs: only `chat_completions` was probed. The
  registry therefore registers only that pair, and a `codex_responses` request for the
  same ID remains a no-op.
- One probe per level, one account, one day. Transient gateway behaviour is not excluded.
