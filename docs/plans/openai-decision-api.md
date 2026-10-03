# OpenAI Decision API integration plan

Deferred plan, 2026-10-03. Start implementation after OpenAI releases the Decision
API and publishes its contract. The operator confirmed it is not released yet.

## Goal and evidence

Add the OpenAI Decision API as an explicitly selected effort scorer for
`hermes-adaptive-effort`, returning a decision through the existing score, label,
route clamp, and request rewrite pipeline.

The current code registers Jev, OpenRouter, and Cloudflare in `scorers.py`.
Adapters expose `classify_detail(prompt) -> (score, failure)` and accept injected
transports and key readers. This is the proposed integration point.

Public OpenAI documentation searches and inspection of the API documentation
index and changelog did not establish its public contract. Its endpoint, model,
authentication, payload, availability, pricing, and retention behavior remain
unverified. All API-specific names in this plan are provisional. Resolve them
from the official release documentation before writing the adapter.

Sources inspected:

- https://developers.openai.com/api/docs
- https://developers.openai.com/api/docs/changelog

## Implementation sequence

1. **Review the release and confirm the API contract.** Check the official
   changelog and API reference when the Decision API becomes available. Verify
   that it supports effort classification using our rubric, rather than assuming
   it is suitable from its name. Verify the canonical endpoint and HTTP method,
   authentication and access requirements, model or decision configuration,
   rubric support, success and error payloads, and score semantics. Confirm that
   one synchronous request can provide a decision within `timeout_s` (currently
   3 seconds by default). If the API requires polling or remote rubric creation,
   revise the design before implementation: request middleware must not create
   remote resources or introduce unbounded waits. Verify remote retention
   separately from the plugin's local no-prompt-storage guarantee.

2. **Add the adapter.** Proposed module: `openai_decision_client.py`, using the
   existing stdlib HTTP transport pattern and injectable transport/key reader.
   Send only text bounded by `prompt_chars`, using the existing effort criteria.
   Expose `classify()` and `classify_detail()`. Resolve the documented credential
   through lazy Hermes `agent.secret_scope`, then environment. `OPENAI_API_KEY`
   is a provisional choice until the actual Decision API authentication is
   confirmed. Return existing failure codes; perform no automatic HTTP retries.

3. **Register and configure it.** Proposed provider value: `openai_decision`.
   Extend `scorers.py` construction, credential presence, effective endpoint,
   and model reporting; extend `plugin.yaml` choices and descriptions. Reuse
   `scorer_model` only if the API actually requires an operator-selected model.
   Add settings only for documented API requirements and keep manifest/defaults
   aligned. Credentials belong in secret scope/environment, never config YAML.

4. **Normalize results and expose status.** Prefer a native numeric score using
   the shared 0..2 rubric. Accept only finite numeric values in range; reject
   booleans, strings, missing answers, and malformed responses. If the API
   returns categorical effort, explicitly map low/medium/high to 0/1/2 and reject
   unknown values. If it returns another numeric scale, agree its conversion
   before implementing; do not clip arbitrary values. Update `command.py`
   endpoint rendering and readiness reporting as needed. Existing status,
   probe, dashboard, and Desktop surfaces should consume the registered provider
   through their current contracts. Add fields only when necessary; never expose
   provider response prose, prompts, secrets, or unrestricted metadata.

5. **Validate the integration.** Add `tests/test_openai_decision_client.py` with
   fake HTTP responses based on the verified contract. Cover request construction,
   text bounds, credentials, score boundaries, malformed results, HTTP failures,
   timeout, and prompt/secret isolation. Extend registry, config schema, command,
   middleware, and dispatcher coverage where needed. Verify one call per turn,
   memoized failure, concurrent claims, independent child mode, unsupported
   request shapes making zero calls, recommend mode making zero rewrites, route
   re-clamping, and actual-change feed behavior. Run the canonical
   `scripts/run_tests.ps1` and `scripts/run_lint.ps1` commands after implementation.

6. **Document and evaluate.** Update README, CONTRACTS, HANDOFF, DEVELOPMENT's
   test inventory, and AGENTS where the provider list or layout changes. Start
   live evaluation with operator-typed probes, then recommend mode. Measure
   latency, timeout frequency, and agreement on representative low/medium/high
   tasks before adopting automatic effort changes. Any cost or cache benefit
   remains unmeasured until a live comparison establishes it. Deployment and
   changes to operator configuration are separate from this planning task.

## Acceptance criteria

- Selecting the new scorer uses only that provider; failures never fall back.
- Default scorer remains Jev; mode and subagent mode remain off by default.
- Errors leave the original request untouched and are memoized within the turn.
- The existing writable-field, thinking-disabled, route clamp, cache safety,
  child classification, and applied-change feed contracts continue to hold.
- Status performs no classification; probes store no decision or prompt text.
- No prompt text or secrets reach logs, reason strings, status, or event feeds.
- Adapter fixtures match verified API examples; canonical tests and lint pass.
- Documentation clearly distinguishes verified functionality from unmeasured
  latency, scoring quality, cost, and cache benefits.

## Decisions still needed

When the API is released, finalize the provider identifier, authentication
variable, model or decision settings, score conversion, and any retention
controls from the published documentation. Record the verified contract and
examples in this plan, then implement the sequence above. If the released API
cannot provide bounded synchronous effort classification, record that finding
and revise the scope before writing code. Release monitoring and automatic
implementation are not configured by this document.
