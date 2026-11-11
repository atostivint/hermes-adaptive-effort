# Domain: route-trial

When is an effort change cache-neutral on the wire, and how is that observed rather than assumed.

## Owns
- `cache_safety.py` - `is_an_effort_change_cache_neutral_on_this_route`, `CACHE_SAFE_API_MODES`.
- `route_trial.py` - the bounded trial of unlisted cache-safe routes from the provider's own cache
  counters. **All heuristic thresholds live here and only here**: 50 % read to count a success, three
  successes to become `observed`, 10 % for `observed_cache_loss`, 5 minutes of gap, five responses
  without counters, 128 rows in `route_verdicts`.
- `tool_errors.py` - fingerprints of repeated tool errors (last 20 results, SHA-256 only) that drive
  `tool_error_escalation` on exact dynamic routes.

## Invariants
- Dynamic capability is exact: an exact registered model/API pair plus positive cache-safety evidence,
  or a positive trial verdict. A cache-neutral API mode alone is never enough.
- Anthropic, cache-hostile and unknown API modes are never trialled.
- A hook or verdict error leaves requests unchanged and the route pinned. Fail-open.
- Memory holds token counts, effort values and request ids - never text. Fingerprints never appear in
  status, logs, history or the feed.
- `dynamic_trial: false` or an unreadable verdict store means `trial_disabled`, not a guess.
- `reset_state()` drops in-memory counters only; verdicts persist in SQLite (`route_verdicts`).

## Known traps
- Thresholds are heuristics, not measurements: change one and you own the argument, in a dated note.
- A rejected level (`400`/`422` on a request this plugin rewrote) is never sent again on that route.
- The trial costs at most one cache miss; that cost and its benefit are **unmeasured**. Do not claim
  savings without a live A/B.
- `post_api_request` / `api_request_error` payloads are additive; read defensively.

## Proof
- `.\scripts\run_tests.ps1 -q -k "route_trial or cache_safety or tool_error"`.
- `.\scripts\run_lint.ps1`.
- For a threshold change: the constant's old and new value, the test that pins it, and a statement that
  no live traffic was measured.

## Open threads
- No live A/B of the trial's cost/benefit.
- Cache counters vary per provider; only observed shapes are handled.
