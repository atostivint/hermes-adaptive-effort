# Design choices

## One job, used every day

Hermes Adaptive Effort chooses the reasoning effort of an outgoing request. The maintainer built it for daily personal use and intends to maintain it through that use. The aim is a small, unobtrusive plugin with a clear purpose and a quiet interface.

That purpose guides the scope: the plugin does not select the conversation model, orchestrate workflows, add tools or redesign Hermes. The Desktop chip, status command and applied-change feed make the existing routing decision visible.

## Separate scoring from answering

The scorer judges task complexity; the conversation model answers the task. Those are independent roles. Jev remains the compatibility default. OpenRouter is an explicit alternative with a configured model, so users control the scorer and its cost.

The rename from `jev-auto-effort` to `hermes-adaptive-effort` expresses this separation. Scorer-specific behavior lives in adapters behind an explicit registry. More providers can be added there without changing the request-rewrite contract, but only Jev and OpenRouter are part of the committed release described here.

There is no cross-provider fallback. Sending task text to another provider, or billing a model the user did not select, should require a deliberate choice.

## Decide for the current turn

Earlier session-wide routing could classify an opening greeting as low effort and carry that decision into a later complex task. Normal `auto` routing therefore keys decisions on `(session_id, turn_id)` and reads the latest user message.

A tool loop makes multiple API requests for one task, so it reuses the decision. Concurrent classifications claim the same key; failed and unsupported attempts are not repeatedly retried within the retained scope. Route changes re-map the stored label to the current model's wire vocabulary.

The scorer receives bounded task text rather than the whole conversation. This limits the data sent and scoring overhead, at the cost of missing context when the latest message relies heavily on earlier discussion. The classifier is a heuristic, not a guarantee of task difficulty or answer correctness.

## Preserve operator intent

Installation does not authorize automatic changes: the default mode is `off`. `recommend` allows inspection before rewriting. Subagents have their own default-off gate because enabling the parent should not silently enable child rewrites.

The middleware rewrites only a field already present in the request. It does not enable reasoning, invent an effort field or modify explicit `none`/disabled thinking. Model capability alone is insufficient; the route must expose usable control.

Slash-command mode changes are process-local. Persistent settings belong to the operator's config or the host's settings interface. This makes a chat command's lifetime explicit and keeps it from quietly editing files.

## Keep failures out of the conversation path

Missing keys or scorer models, HTTP errors, timeouts and malformed scores leave the original request unchanged. A finite numeric score in `0..2` is required; booleans, non-numeric values and out-of-range scores are invalid.

This protects the conversation from scorer failures, but cannot eliminate inaccurate vendor capability information. Effort labels are clamped through Hermes and narrow vendor mappings; the known Ox Alpha rejection remains documented. Fail-open scoring is not a promise that every vendor will accept every rewritten request.

## Make cache behavior explicit

Effort settings can affect a provider's prompt cache. `auto` prioritizes per-turn classification. `cache_safe` uses per-turn decisions only on recognized cache-neutral API modes; on other or unknown modes it pins a decision to the session while that entry remains cached.

This is deliberately conservative. State is bounded and in memory, so eviction, reload or reset ends that retention. Cache-hit rates and net cost effects need live measurements; the repository does not present expected savings as proven results.

## Observe the result, avoid storing prompts

Status contains allowlisted effort and routing metadata. The applied-change feed records only values that actually reached a rewritten request, not recommendations or no-op decisions. It contains no prompts or conversation identifiers.

The Desktop chip uses separate conversation-scoped status so changing chats does not display another chat's effort. Completed-turn results remain available in the bounded ledger; actual finalize/reset events clear them.

Normal prompts are not persisted. Child goals exist transiently in memory because the parent-written goal is the child classifier's input. Selected external scorers receive task text, so their own retention policies still apply.

## Evidence and maintenance

The test suite blocks network access and uses injected scorer transports. It checks real Hermes discovery and middleware dispatch, field preservation, turn reuse, route mapping, settings parity, public schemas and failure paths.

Tests verify implementation contracts, not real-world savings. The dated reviews remain as historical evidence, with current behavior documented separately. The plugin uses some Hermes internals through lazy imports; host updates therefore deserve compatibility checks. A live OpenRouter evaluation and cost/cache A/B measurements remain open.

## Proposed provider work

Project memory records a Cloudflare Clef scorer proposal; concurrent adapter work began during this documentation refresh. It is work in progress, not shipped support in the release documented here. The committed provider registry at the start of the refresh (`20fe996`) contains only Jev and OpenRouter. Any additional adapter must preserve explicit selection, the shared score validation, fail-open behavior, credential isolation and bounded per-turn calls.
