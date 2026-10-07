# Codex quota and catalog audit — 2026-10-07

## Outcome

Hermes CLI/catalog access and account matching are now confirmed. At the snapshot recorded here, no inference call had been sent. This is an access and quota-monitoring record, not a compatibility result.

## Verified facts

- At campaign authorization, the Codex app meter showed **31% used** on its 300-minute window; the latest observed meter was **34%**.
- The Codex app and Hermes use the same account. No account identifier is recorded here.
- Hermes CLI is accessible through its launcher. The authenticated catalog returned: `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`.
- At this audit snapshot, **no inference calls had been sent**.
- The hard Codex meter maximum is **61%**; the operator's stop threshold is **60%**. Monitor quota separately from dollar spend and stop before reaching 60%.
- A hard pre-send, per-request Codex quota bound that includes retries is still not demonstrated.

The Codex envelope is a maximum increase of **30 percentage points** from the 31% authorization reading, with an operational stop at 60% and a hard maximum of 61%. It is a quota limit, not a dollar budget or reservation. The independent dollar caps remain **$2 for OpenRouter** and **$3 for OpenCode Go**.

## Decision

This snapshot records the campaign as authorized with zero inference calls sent so far; it does not establish request-level quota enforcement or any model compatibility result. No account identifiers, credentials, prompts, or raw error details are retained. Machine-readable facts are in `audit.json`.
