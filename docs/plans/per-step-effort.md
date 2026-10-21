# Per-step effort inside the tool loop (design note, issue #70)

Status: desk analysis. No runtime or test change. Every number below is either read from code
or marked **unmeasured**; no live call was made for this note.

## Question

Today one effort decision is taken per user turn and reused through the whole tool loop
(invariant 4). The only change inside a turn is the local, up-only tool-error escalation. A turn
that starts with a hard planning step and continues with mechanical tool calls runs every step at
the planning level, and the reverse also happens. Ares ([arXiv 2603.07915](https://arxiv.org/html/2603.07915v1))
reports large token savings from choosing effort per agent step, with a fine-tuned local router
that shares the agent's KV cache. Should this plugin choose effort per step?

## What happens in the tool loop today

Read from `middleware.py`:

- `_handle` keys the turn memo by `(session_id, turn_id)` (`_decision_key`). When the memo is
  `decided` with a label, the request takes the `entry = turn_entry` path and no scorer call is
  made; a route change inside the turn only re-clamps the label (`_target_for_route`).
- A scorer call happens only when the memo has no label. It is guarded by `_claim_scoring`
  (`_IN_FLIGHT` for the turn, `_ROUTE_IN_FLIGHT` for retained-route scopes), bracketed by
  `_classify_timed` (latency histogram) and `_scorer_breaker_record` (circuit breaker), and a
  paused breaker marks only the turn memo as `scorer_paused`.
- The one in-turn change is `_escalated_target`, applied just before the single return point,
  gated by `tool_error_escalation`, `dynamic_route`, non-empty `route_choices` and a non-Anthropic
  provider. It reads the request locally through `tool_errors.repeated_error_fingerprints`
  (current user turn only, last `LOOKBACK_RESULTS` = 20 tool results, SHA-256 fingerprints,
  edit calls = `tool_errors.EDIT_TOOL_NAMES` = `write_file`, `patch`) and raises one level per
  repeated fingerprint via `_effort.raise_choice_level`.
- The rewrite goes through `_record_effort_change`, which deduplicates on
  `(decision_key, from, to)` and feeds the applied-change feed (ring of 64).

## Q1. Latency

A per-step scorer call adds the scorer round trip to every tool iteration, because the request
cannot be sent before the level is known (the middleware is synchronous in `on_llm_request`).

- Deadlines: `scorers.PROVIDER_TIMEOUT_S` is Jev 2.0 s, OpenRouter / Cloudflare / OpenAI Decisions /
  Nace 3.0 s, custom 10.0 s. These are ceilings, not latencies. The source comment states they are
  not measured on this plugin's traffic.
- Measurement tools exist: the in-process histogram (`scorer_latency_state`, buckets 100 ms to
  10 s, counts only, cleared by `reset_state()`) and `scripts/run_scorer_eval.py` (`p50_ms`,
  `p95_ms`, served by `GET /eval`). The repository holds no committed scorer-eval result and no
  recorded histogram, and the eval corpus is synthetic single prompts.
- Therefore: **p95 per provider is unmeasured**, and typical tool-step time on the target models
  is **unmeasured** too.
- Structural consequence, independent of the numbers: a per-step call is added once per tool
  iteration, so a 20-step turn pays the round trip 20 times instead of once. The retry rule
  (`scorer_retry.py`, one retry within `timeout_s`) bounds a single call but not the sum. A
  timeout fails open to the previous level, so the failure mode is wasted time, not a wrong
  request.
- Ares's gains come from a local router sharing the agent's KV cache and report no router
  latency. A remote scorer does not have that property.

## Q2. Cache

- `cache_safety.CACHE_SAFE_API_MODES` (`codex_responses`, `chat_completions`) states that effort
  is a request field, not prompt text. This is reasoning from the transport shape, not a measured
  cache hit rate. `docs/CONTRACTS.md` explicitly claims no live cache-hit, cost or quality A/B.
- `route_trial.py` judges the provider's own cache counters, but its state is keyed by
  `(session_id, route_key)` only. `note_request` computes `changed = previous != level` against
  the previous request of that session and route, with no turn identifier, and `on_response`
  counts a success whenever a change keeps at least `KEPT_RATIO` (50 %) of the previous read.

**Resolved question: does `route_trial` distinguish within-turn from turn-boundary changes?**
No. It sees a sequence of requests and compares consecutive efforts; it has no notion of a turn.
Consequences found in the code:

1. Today this does not matter. A trial route (`trial_route` in `_handle`) has no named choices
   (`route_choices` empty), so `tool_error_escalation` is skipped, and the decision is reused
   through the loop. Trial requests inside a tool loop all carry the same level, so
   `changed` is false and they only establish `baseline_ok`. Every counted change is a
   turn-boundary change, or a route re-clamp.
2. Listed dynamic routes (`LISTED`) never enter the trial (`_TLS.trial` is set only for trial
   routes), so the within-turn escalation on listed routes produces no cache evidence at all.
   Its cache neutrality rests on the API-mode argument alone.
3. With per-step levels the trial would count within-turn changes as ordinary evidence.
   Within a tool loop the prompt is append-only and requests are seconds apart, so those
   comparisons would pass the `CACHE_TTL_S` (300 s) and `HISTORY_REWRITE_RATIO` guards and be
   more conclusive than turn-boundary ones, but nothing would label them, and three of them
   from a single turn could grant `observed` for the route. That is arguably valid evidence
   for per-step changes, but it would also certify per-turn behaviour from per-step samples.
   Any implementation would need to tag the pending record with within-turn vs boundary.

Whether per-step changes are cache-neutral on each exact dynamic route (`openai-codex/gpt-6.1-sol`,
the `opencode-zen` Muse Spark routes, the `opencode-go` injection routes) is **unmeasured**.
Anthropic is out of scope: a marker inside a tool loop does not take effect until the next user
turn (`docs/CONTRACTS.md`, per-message section), so no per-step path exists there.

## Q3. Local signals only

Cheap signals available without a scorer call and without sharing anything:

| Signal | Source | Note |
| --- | --- | --- |
| Step index in the turn | count of tool results after the last turn boundary (`tool_errors._events_from_messages` already walks this) | Ares reports low effort early, high later; direction is not guaranteed for coding agents |
| Last tool name | assistant `tool_calls` / `tool_use` / `function_call` names | Needs a mapping from Hermes tool names to expected difficulty; none exists |
| Previous call was an edit | `EDIT_TOOL_NAMES` | Already used, only as the separator for repeated errors |
| Repeated error | `repeated_error_fingerprints` | Implemented (issue #34), up-only |

Rule shape, if pursued: a pure function in `tool_errors.py` (or a sibling) returning a level
offset, evaluated at the same point as `_escalated_target` and applied through the single
rewrite return point, so the feed, history store and status fields work unchanged. It would stay
within the route's own `route_choices`, and never touch `none` / `ultra`.

Limits: signals decide the direction of a change relative to the turn's decision, they do not
know the task. A pure down-step on "mechanical" tools is the risky half, because it lowers
reasoning exactly when the observation might need it, and no accuracy measure exists in this
repository. The expected share of Ares's gain that local signals could capture is **unmeasured**.

A method to find out without new runtime code: replay recorded request shapes (roles, tool
names, fingerprints and counts only, no text) through candidate rules and count how often each
rule would have changed the level. That measures frequency of change, not quality or cost, and
is not part of this issue (no replay script, per the approved plan).

## Q4. Contract changes per option

Option A, do nothing: no change.

Option B, local signals (no scorer call):

- Invariant 4: unchanged for scorer calls. The note on in-turn changes grows from "the local
  up-only escalation" to "local rules", and "never down" in invariant 11 needs an explicit
  amendment if a down-step is allowed.
- Invariant 8: unchanged, nothing is shared.
- Invariant 11: the escalation paragraph gets a sibling rule with the same scope (exact dynamic
  routes, non-Anthropic, no trial routes); fingerprints-only memory stays the rule.
- Invariant 12 and the breaker/histogram: unchanged. The feed `(decision_key, from, to)`
  deduplication would suppress a second identical transition in one turn; with multiple
  distinct steps (for example `medium -> low -> medium`) each pair is distinct, but returning
  to a value already recorded for that decision key is swallowed, so the feed would
  under-report. That needs a decision before implementation.
- Status and Desktop v2: additive reason code next to `escalation_reason`.

Option C, per-step scoring:

- Invariant 4: "at most one effort scorer call per turn" is replaced by a per-step budget, with
  hysteresis memory and `_IN_FLIGHT` keyed per request instead of per turn.
- Invariant 8: a per-step call would share the current observation. Tool results must never be
  shared, so the call could only carry the user task plus tool names and counts, which is much
  less than what Ares's router uses and likely removes the benefit. Anything wider needs
  an explicit consent contract, a separate setting and new documentation.
- Invariant 11: tool-error escalation is subsumed or must be ordered against the scorer
  (local raise vs scored level); Anthropic stays excluded.
- Invariant 12 and breaker: each call counts once in the breaker and histogram, so a long loop
  can open the pause within one turn; the feed deduplication issue above applies, and the ring
  of 64 fills faster.
- `route_trial`: needs the within-turn tag from Q2.
- Cost: an extra scorer bill per step, **unmeasured**, plus the latency in Q1.

## Recommendation

**Do nothing for now; keep the existing escalation; run one cheap measurement before any local-signal work.**
Per-step scoring (C) is not justified: latency and scorer cost scale with steps, the privacy
rule forbids sending the observation that the paper's router relies on, and no cache or quality
evidence exists. Local signals (B) are the only plausible next step, but their benefit is
unmeasured and the downward half is risky.

### Optional follow-up: within-turn cache A/B (not started)

Per the approved plan, a within-turn cache A/B can later be run cheaply on
`opencode-go/deepseek-v4.1-flash` using the replay tooling of issue #71. Proposed protocol: replay a fixed tool-loop transcript of
at least 8 steps against the route twice, once at a constant level and once with the level alternated
between two allowed values at every step, reading `cache_read_tokens` from the
response usage, repeated enough times to estimate spread (count to be fixed by the operator's
budget).

Go criteria for pursuing local signals (B):

- Median cache read of the alternating run stays at or above 90 % of the constant run, and no
  step falls under the `route_trial.LOSS_RATIO` (10 %) threshold on a baseline that kept its cache.
- No 400/422 for any level used.
- The replay of candidate rules on recorded request shapes shows the rule would change the level
  in a meaningful minority of steps (threshold to be set by the operator; a rule that fires on
  nearly every step or almost never is not worth the code).

No-go (stay at option A): cache read of the alternating run falls clearly below the constant
run, rejections appear, or the rule frequency is negligible. Per-step scoring (C) would
additionally need a measured p95 well under typical step time, an agreed payload that excludes
tool results, and an accuracy/cost comparison; none exists, so C stays out of scope.
Thresholds other than the `route_trial` constants are proposals, not measured values.
