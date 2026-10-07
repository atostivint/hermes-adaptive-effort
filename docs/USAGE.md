# Usage and troubleshooting

[Quick start](../README.md#quick-start) · [Configuration](CONFIGURATION.md) · [Runtime contracts](CONTRACTS.md)

## Commands

Type these in a Hermes chat served by the enabled plugin:

| Command | Result |
| --- | --- |
| `/hae` | Show the 10 most recent applied changes for this conversation |
| `/hae history [N\|all] [full]` | Show N recent changes, all retained changes, or expanded details |
| `/hae help` | Show usage |
| `/hae status` | Read a concise summary of mode, scorer, model, effort and cache compatibility |
| `/hae status full` | Read expanded settings and current-conversation decision details |
| `/hae status json` | Read the machine-readable status payload |
| `/hae probe <text>` | Score the text you type without changing a request or storing a decision |
| `/hae auto` | Use dynamic decisions on verified routes and retained decisions elsewhere |
| `/hae once` | Retain one decision per model and route |
| `/hae always` | Classify each new user turn on eligible requests |
| `/hae off` | Stop scoring and rewrites for future requests |

Mode commands apply only to future requests in the current process. For a persistent default, save the mode in [plugin settings](CONFIGURATION.md#save-settings). Unknown mode/command arguments return usage without changing state. A bare `probe` requires text.

The default history view lists the local time, target model, applied effort transition and cache behavior. `Compatible` means the transport carries effort outside the cached prompt; `Sensitive` means changing effort can affect cache continuity; `Not verified` means the plugin has no evidence either way. Cache compatibility is separate from whether a model/API pair is registered for dynamic classification. `/hae history full` adds provider, API route, score/label, scorer and timing details. The history records applied request changes only, so failed, unsupported, no-op and off-mode requests do not appear.

History is scoped to the active conversation and Hermes profile. The profile journal keeps at most 64 changes per conversation and 64 conversations. It stores model and decision metadata but no task or prompt text, survives process restarts, and is erased when that conversation is explicitly reset. If the host profile does not offer writable plugin storage, routing remains fail-open and history reports unavailable.

## Choose a mode

Use `auto` when you want the plugin to select a decision scope from its exact route registry and cache-safety checks. On a verified dynamic route, including eligible Claude per-message requests, a new message can receive a fresh classification. Other routes retain their decision for the conversation.

Use `once` when you want one classification per exact provider/model/API route. A later change in task complexity does not cause a fresh classification on that retained route.

Use `always` when each new message should be classified on every eligible route. This includes routes whose cache behavior does not justify dynamic decisions in `auto`.

Use `off` to leave requests untouched. Status and the Desktop popup can still show an observed route. Selecting a scorer while off sends nothing; an explicit probe still scores the supplied text.

### Example: a greeting followed by a complex task

| Situation | `auto` | `once` | `always` |
| --- | --- | --- | --- |
| First eligible message on route A | Classify | Classify | Classify |
| Later message on the same route | Classify if A is registered dynamic and cache-neutral; otherwise reuse | Reuse | Classify |
| First eligible message on a new route B in a later turn | Classify, unless B already has a retained decision | Classify, unless B already has a retained decision | Classify |
| Another request in the same tool loop | Reuse this turn's label and map it to the current route | Same | Same |

For example, `once` can retain a low decision from an opening greeting when a later task becomes more complex. The dynamic branch of `auto` and `always` reevaluate the later user message. The scorer is heuristic; these examples do not prescribe a guaranteed score.

All reuse depends on the decision remaining in bounded memory. Reset, reload or eviction can require another classification. With no turn ID, active modes use retained session/route scope. The [sequence diagram](CONTRACTS.md#turn-reuse-and-route-changes) shows tool-loop behavior.

### Subagents

Set `subagent_mode` independently if child routing is wanted. Both the main mode and the child mode must be active. The plugin classifies a registered child from the goal written by its parent, using the same route and failure rules.

## Check readiness without spending a scorer call

```text
/hae status
/hae status json
```

`/hae status` shows the selected scorer/model and credential readiness, then the active conversation's target model, decision and cache behavior. Use `/hae status full` for the effective endpoint, settings and expanded decision fields. `credential: not required` is expected for custom auth `none`.

The machine-readable status distinguishes the method (`decision_type`) and, for named choices, the route's `choices`. A native-choice or fixed decision has `score: null`; its `label` is the selected name and `target` is the value actually applied. A legacy-score `label` and route `target` can differ: on a low/high/max route, a medium label can map to high. `requests` and `probes` count different things; tool-loop requests can reuse one scoring attempt. A recorded probe attempt does not prove that HTTP occurred, because missing credentials fail before transport.

Status contains metadata and decisions, not prompt or guidance contents. It neither classifies the current conversation nor proves upstream availability.

## Try an explicit probe

```text
/hae probe What is 2 + 2?
```

Probe sends only the text you type and any configured classifier guidance to the selected scorer. It may incur a charge even while routing is off. It never attaches target context, changes a model request or stores a routing decision.

A successful probe confirms that this scorer accepted that input. It does not establish whether the conversation model's route accepts effort controls, or measure answer quality and savings.

## Desktop and terminal

The optional Desktop extension adds an **Effort** chip. Its compact **Routing mode** popup contains the four modes; hover over a mode for its explanation. **More** includes recent applied changes for the focused chat, scoped to that backend and profile.

**More** reveals the focused chat's effort and route, scorer/model readiness, guidance presence and gateway status. **Activity** expands aggregate counters and the latest global result. Closing the popup returns both disclosures to their compact state.

The chip follows the focused chat and backend/profile. A completed turn can retain its last decision; actual session finalize/reset clears it. `N/A` means the focused chat has no usable decision, is unsupported/in flight, or cannot be matched to the backend state. Read the details for the reason.

For the focused chat:

- An actual applied rewrite produces one notification with the `from → to` transition.
- A newly observed failure or unsupported request produces one warning explaining that the request continued unchanged.
- These notifications stay outside transcript history. Background chats do not trigger them.

A fresh applied rewrite can also synchronize Hermes' native reasoning selector for that same focused session. This is a session setting; it does not write the global configuration. Background changes, repeated events and unchanged values do not trigger synchronization. A manual selector choice remains until a later fresh rewrite. Switching routing off does not restore the previous selector value. On Claude's per-message cache-preserving path, the top-level initial effort stays unchanged, so the selector is deliberately not synchronized; the request carries the selected level in its turn marker.

Child-isolated sessions cannot be synchronized through the parent's session API. A rejected or unconfirmed selector update produces a notice; the model request already sent is unaffected.

Set `show_desktop_popup: false` to hide the chip, popup and notifications. Session-scoped selector synchronization operates independently of that display switch. Set `show_tui_status: false` to hide the terminal status item; the terminal setting is read on the next outgoing request.

The terminal can also report an applied transition such as `Effort changed: high -> low`. A matching target produces no rewrite and no change notification.

### Enable the Desktop surface

Enable the extension in Desktop's plugin controls. The backend agent plugin must also be enabled. If the host does not discover the payload's `desktop/` extension, deploy that extension separately to the host's `desktop-plugins/hermes-adaptive-effort/` directory, then reload/reconnect the relevant Desktop connection.

Hermes needs plugin middleware and lifecycle hooks. Desktop additionally needs its plugin SDK and focused-conversation support. The [development guide](DEVELOPMENT.md) describes host compatibility checks.

## Troubleshooting

Start with `/hae status`. Do not probe private conversation text to diagnose an unsupported route; eligibility checks happen before scorer transport.

| What you see | Meaning | Next check |
| --- | --- | --- |
| `off` | Routing is disabled | Choose a mode; save it if it should survive restart |
| Required credential missing | The serving process cannot find the selected key | Check the right profile/service environment and restart |
| `model_missing` | OpenRouter/custom has no scorer model | Set the exact `scorer_model` |
| `account_missing` / `account_invalid` | Cloudflare account ID is absent or not 32 hexadecimal characters | Correct `cloudflare_account_id` |
| `endpoint_missing` / `endpoint_invalid` | Custom URL is absent or invalid | Supply the complete HTTP(S) route without embedded credentials/fragments |
| `unsupported_api_format` / `unsupported_auth` | Custom format or auth value is unknown | Use `systemone`/`chat_completions` and `none`/`bearer` |
| `failed` with `timeout`, `http_error` or `transport_error` | Scorer request failed | Check the selected endpoint/access; consider cold-model startup and `timeout_s` |
| `malformed_response` | Score or named choice does not match the adapter contract | Check the exact allowed choices for that route, or the finite `0..2` score contract for legacy routes and probes |
| `unsupported`, `effort_control_unsupported`, zero probes | No writable control or authorized missing-field insertion | Check the exact route in [Compatibility](MODEL_COMPATIBILITY.md) |
| `reasoning_disabled` | Reasoning is explicitly disabled | Review the original model setting; routing preserves it |
| `effort_value_unsupported` | The route cannot express the selected label | Check the route vocabulary |
| `decided` but no change notification | Target may already match, or labels map to the same wire value | Compare `label`, `target` and the incoming effort |
| Desktop `N/A` | No usable focused-chat decision or backend state | Open **More**; check connection, profile, enabled plugin and extension |
| Mode returns to an earlier value after restart | Chat override was not persisted | Save the default in plugin settings |

Failed decisions are retained in their selected scope; repeated tool requests do not retry them. With retained route scope, another user message can reuse that failure. Start a new conversation after correcting a configuration problem if you need a new route decision. Reload/reset and bounded-memory eviction also end retention.

Reasoning capability, a model profile or a generic OpenAI-compatible endpoint alone does not prove effort support. `effort_models` is an advanced operator assertion, not a repair for every unsupported route. Check the provider's current catalog and the exact route evidence before asserting support.

## Update, disable and recover

```bash
hermes plugins update hermes-adaptive-effort
hermes plugins disable hermes-adaptive-effort
```

Restart the serving agent after an update or enable/disable change. For an immediate process-local stop, use `/hae off`; save `off` as the persistent mode if it should remain disabled after restart.

Read the [operator handoff](HANDOFF.md) for dated deployment/recovery evidence. Those host observations are not a fresh health check of your installation.

## Migrate from jev-auto-effort

1. Install the current repository-root payload.
2. Move settings from `plugins.entries.jev-auto-effort.settings` to `plugins.entries.hermes-adaptive-effort.settings`.
3. Disable the old plugin ID and enable `hermes-adaptive-effort`.
4. Replace the old Desktop extension and restart the serving processes.
5. Use `/hae` and the canonical `auto`, `once`, `always`, `off` mode names.

For a managed install, update source metadata to the renamed repository. The former `#hermes-adaptive-effort` suffix refers to an old nested payload; current installation uses the repository root.
