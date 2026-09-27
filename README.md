# Jev-Auto — Task 1: architecture verification + minimal working scaffold

Kanban card `t_dc768725`, plan `/root/.hermes/plans/2026-09-24_164819-jev-auto-effort.md`
(Task 1 only: gates A and B). Everything below was read out of the installed
source at `/usr/local/lib/hermes-agent` (git checkout, `main`) and the installed
plugins under `/root/.hermes/plugins/`. Nothing was inferred.

## Gate A — can plugin code access the LLM request middleware? YES

Registration surface (`hermes_cli/plugins.py:917-921`, `hermes_cli/middleware.py:27-66`):

- `ctx.register_middleware("llm_request", callback)`; valid kinds are
  `llm_request`, `tool_request`, `llm_execution`, `tool_execution`
  (`VALID_MIDDLEWARE`, `hermes_cli/middleware.py:24`).
- Valid hooks (`VALID_HOOKS`, `hermes_cli/plugins.py:108+`) include
  `on_session_start`, `on_session_end`, `on_pre_agent_start`, `pre_api_request`,
  `post_api_request`, `turn_end`, `tool_post_process`, … — `on_session_end`
  exists, so in-memory state has a verified cleanup path.

Invocation contract — the context keys actually passed at
`agent/turn_api_request.py:143-148`:

```
session_id, task_id, turn_id, api_request_id, platform, provider, model,
base_url, api_mode, api_call_count
```

plus `request`, `original_request`, `middleware_schema_version`,
`telemetry_schema_version`, `_meta`. Full request payload is validated by
`utils.request_payload()` (`hermes_cli/middleware.py:27-66`) — `messages` and
`model` required, `dict` payload, 50 MB cap.

Return semantics (`website/docs/developer-guide/middleware.md`, verified against
the dispatcher): return `{"request": <full replacement>, "source": …, "reason": …}`
to change the request, `None` to leave it byte-for-byte untouched. Middleware runs
after payload sanitisation and **before** `sanitize_outbound_kwargs` / the actual
send (`agent/turn_api_request.py:143-151`), so a rewrite lands on the wire.
Exceptions escape as a failed middleware attempt; the request survives without a
rewrite (fail-open), and every result is recorded as `middleware_trace`.
"Return complete replacement payloads, not partial patches" is the documented
rule — `_apply()` copies the payload and changes exactly one value.

Key resolution at register time (not import time):

```python
def register(ctx):
    ctx.register_middleware("llm_request", on_llm_request)
    ctx.register_hook("on_session_end", on_session_end)
```

`PluginContext.get_config(key)` reads
`plugins.entries.<plugin-id>.settings.<key>` on every call
(`hermes_cli/plugins.py:269-279`), so a config edit applies without a restart.

### Jev access — available, with one caveat

No core `ctx` API exposes Jev: there is no `ctx.jev`, and `ctx.llm` is a chat
completion helper (the Jev client in the installed plugin refuses non-approval
prompts). The verified route is plain HTTP against the Jev decision endpoint,
reusing the transport shipped by the **installed, supported** `jev-approvals`
plugin (`/root/.hermes/plugins/jev-approvals/__init__.py`):

| Item | Verified value | Source |
| --- | --- | --- |
| Endpoint | `https://api.typesafe.ai/v1` + `/systemone` | `DEFAULT_BASE_URL`, `_route_for()` in `jev-approvals/__init__.py` |
| OpenRouter alternative | `https://openrouter.ai/api/alpha` + `/decisions` | same file |
| Body | `{"state": {…}, "model": "jev-latest", "questions": {…}}` | `QuestionsClient.ask()` |
| Score answer | `{"answers": {"<key>": {"score": <float>}}}` | `_score()` / `_Answer` |
| Credential | `TYPESAFE_API_KEY` (TypeSafe route) | `SENTINEL_ENV` |
| Credential resolution | `agent.secret_scope.get_secret()` → env | plugins never read raw env for secrets |

`TYPESAFE_API_KEY` **is** configured in `~/.hermes/.env` (verified by name only).
Caveat: the decision service is a *private/closed* provider, so availability is
not provable offline — `JevClient.classify()` returns `None` when the credential
is absent and on every transport/schema failure, and no test opens a socket.

## Gate B — verified provider-specific effort fields

Only two payload shapes are treated as authoritative, because both are the shapes
Hermes itself writes (all read in `agent/transports/chat_completions.py`):

| Shape | Where Hermes writes it | Lines |
| --- | --- | --- |
| `request["extra_body"]["reasoning"] = {"enabled": bool, "effort": str}` | OpenAI-compatible reasoning routes, legacy and provider-profile paths | 199-204, 213-220, 528 |
| `request["reasoning_effort"] = str` | `model.startswith("kimi-k3")` → `low/high/max`; `kimi-k2` → `low/medium/high`; `provider=="tokenhub"`; `provider=="lmstudio"` | 495-504 |

Related but **not** used: `extra_body.thinking_config` / `thinking_config`
(Gemini, camel vs snake on `model.startswith("gemini-4.6-flash")`), 174-205;
Anthropic `thinking: {"type": "enabled", "budget_tokens": N}` computed from
`reasoning_config` (`agent/anthropic_adapter.py:1406+`) — a token budget, not an
effort level, so this plugin does not touch it.

Allowed values come from Hermes' own tables, never from plugin-local guesses:

- `agent.reasoning_effort.EFFORT_LADDER = ("none","minimal","low","medium","high","xhigh","max")`
  and `OPENAI_COMPAT_WIRE_EFFORTS = ("low","medium","high","xhigh","max")` (58-85).
- `clamp_effort(effort, allowed)` — never an unsupported wire value (47-75).
- `route_supported_efforts(provider, model)` — per-route vocabulary
  (153+, supported words parsed out of `supported_reasoning_efforts` docs).
- Core precedent for "route words only when the prompt/tool supplied them":
  `transform_to_claude45()` (`agent/chat_completion_helpers.py:742-799`).
- Debug surface only, not an input: `hermes reasoning_effort --provider … --model …`
  (`hermes_cli/reasoning_effort_cli.py:27-78`) and
  `/dev/debug/reasoning-effort?provider&model` (`hermes_cli/debug_routing_http.py:148`).

`effort.py` encodes this: Jev's ordered 3-level score rubric → `low|medium|high`
(thresholds `<0.5`, `0.5..<1.5`, `>=1.5`; out-of-range/NaN/bool/str → invalid),
then `clamp_effort()` onto `route_supported_efforts()`.

## Fail-open rules implemented

| Condition | Behaviour | Test |
| --- | --- | --- |
| `mode` unset / invalid (`off` is default) | `None`, no state, no Jev call | `test_default_off_never_classifies`, `test_invalid_mode_is_off` |
| No `session_id` | `None`, no Jev call | `test_missing_session_fails_open` |
| No writable effort field (incl. `enabled: false` / `"none"`) | mark session `unsupported`, `None`, no field invented | `test_auto_never_adds_an_effort_field`, `test_disabled_reasoning_is_never_re_enabled` |
| Missing credential / timeout / transport error / malformed answer | mark `failed`, `None`, request unchanged; no retry in-session | `test_missing_key_never_builds_a_request`, `test_timeout_returns_none`, `test_classifier_*`, `test_failed_session_is_not_retried` |
| Jev label not mappable onto the route vocabulary | mark `unsupported`, `None` | `test_map_effort_*` |
| Any unexpected exception in the middleware | `None` (never raises) | `_handle` wrapped in `on_llm_request` |
| First prompt missing from `messages` | `None` | covered by the unsupported path |
| `subagent_mode` unset / invalid (`off` is default) | children are not re-written, even when `mode: auto` | `test_child_default_off_never_classifies`, `test_subagent_mode_off_leaves_children_alone` |
| `subagent_start` without a `child_goal` | child not registered; it falls back to its own prompt | `test_subagent_start_without_goal_is_not_registered` |

Bounded state: `OrderedDict` keyed by `session_id`, FIFO-capped
(`max_sessions`, default 64), cleared by `on_session_end`. Entries store only
`state`/`label`/`target` — **no prompt text is ever persisted** (asserted by
`test_no_prompt_text_is_stored`), and reason strings carry effort values only.

## Cache safety, and the `cache_safe` mode

Effort is a **request field**, not prompt text, on the routes that matter here —
verified live: `ResponsesApiTransport.build_kwargs` returns an identical
`prompt_cache_key` for `low` and `high`, and the effort string never appears in
`input` or `instructions`. Changing it mid-session costs nothing on those routes.

That is **not** a provider-independent rule. The Anthropic family renders its
thinking configuration into the prompt and documents that changing it
invalidates message blocks; its transport carries `thinking.budget_tokens`
rather than an effort level at all, so an effort rewrite there is not merely
cache-hostile, it is meaningless.

So there are three routing scopes and one per-route table:

| mode | scope | on `codex_responses` | on `anthropic_messages` |
|---|---|---|---|
| `off` | nothing | no call | no call |
| `recommend` | per turn, reports only | per turn | per turn |
| `auto` | per turn, rewrites | per turn | per turn |
| `cache_safe` | per turn **only where the cache survives** | per turn | pinned to the session |

`cache_safe` is the default I would actually install. An unrecognised route is
treated as **unsafe** and pins the session: guessing wrong the other way silently
degrades the cache, which only shows up in the bill, whereas pinning merely costs
one level of adaptation. `/jev-auto status` prints the verdict and its reason.

A turn that already sits at the level Jev picked costs no rewrite at all — the
middleware reports no decision rather than a rewrite identical to its input.

**Not measured:** the plugin's real effect on `cache_read_tokens` on this box. It
is not installed, so the only way to know is to run it and compare the
`session_model_usage` columns before and after.

## Effort is classified per USER TURN, not per session

One decision per session is the wrong granularity for an interactive session:
a conversation opening with "hey" would freeze `low` onto every later question —
verified, and it silently capped a multi-region redesign at `low` for the rest of
the session.

`_bind_turn_identity` (`agent/turn_context.py:536`) mints a **fresh `turn_id` per
user message**, and it stays constant for every API request of that turn. So the
memo key is `(session_id, turn_id)`, which gives both properties at once:

* each user message is classified afresh;
* a tool loop inside one turn (several API requests, `api_call_count` 1..N) still
  reuses one decision — and therefore still costs a **single Jev call**.

A subagent is one session and normally one turn, so per-turn granularity does not
increase its cost either: still one call per subagent.

`max_sessions` was renamed `max_turns` (same default, 64) because it now bounds
turn decisions. `on_session_end` clears every turn of that session, and the child
registry keeps its own FIFO rather than sharing the decision list.

## Subagent effort routing

A subagent is classified from **the goal its parent wrote**, not from its own
first prompt. The parent's `goal` is the terse, self-written description of the
work — the same thing a good parent writes when it knows the sub-task is
mechanical.

Two verified facts make this work without touching Hermes core:

1. `subagent_start` is emitted with `parent_session_id`, `child_session_id` and
   `child_goal` (`tools/delegate_tool.py`), **before** the child's first request
   is submitted — the hook runs at the end of child construction, while the turn
   itself goes out later from `delegate_tool_child_run.py`. Nothing is missed, so
   a one-request subagent is still caught.
2. A child is its own `AIAgent` with its own `session_id` (`is_delegated_child_context`),
   which is the only reliable way to tell a child's requests from the parent's.

So the registry is `child_session_id -> goal`, populated by `subagent_start`,
trimmed by `subagent_stop` and by `on_session_end`, and bounded by the same
`max_sessions` cap. `subagent_mode` is a **second, independent gate**: enabling
`mode: auto` never silently starts rewriting children's effort.

Cost: exactly **one Jev call per subagent**, not per request — the decision is
memoised per session, and a subagent is one session.

### The `codex_responses` effort shape

Effort reaches the wire in three different places depending on the route, and
reading one shape while writing another produces a **silent no-op**: the plugin
reports a decision, the request looks rewritten, and the provider never sees it.
All three are now handled by the same read/write pair:

| Route family | Slot |
| --- | --- |
| `extra_body.reasoning.effort` (chat_completions, anthropic) | nested |
| `request["reasoning_effort"]` (kimi, tokenhub, lmstudio) | top-level flat |
| `request["reasoning"].effort` (**codex_responses** — the delegation default) | nested top-level |

The third shape was verified live against `ResponsesApiTransport.build_kwargs`,
which returns `reasoning={"effort":…,"summary":…}` with `extra_body` absent. It
is the shape every subagent actually uses, and the old `_apply` wrote it into
`extra_body` — where the transport drops it. `_apply` now writes back into
whichever container `_effort_slot` actually read, so a readable slot is always a
writable one, and a sibling key (`summary`) is never dropped.

## Files

The plugin payload lives in `jev-auto/` — the exact directory name it is
installed under (`~/.hermes/plugins/jev-auto`) — so the repository root stays a
plain project container. That matters for the test run: pytest imports a
directory's `__init__.py` as a package only when the directory name is a valid
Python identifier (`resolve_package_path()`), so a payload sitting directly in
`jev-auto-plugin/` (dashes) makes `pytest tests` fail while collecting the root.

```
jev-auto/plugin.yaml    manifest: kind standalone, capability llm_request
jev-auto/__init__.py    register(ctx) -> middleware + on_session_end
jev-auto/effort.py      rubric -> label -> clamped wire value (pure)
jev-auto/jev_client.py  JevClient: bounded, validated, fail-open, injectable transport
jev-auto/middleware.py  on_llm_request + session state + fail-open table above
tests/conftest.py      loads the plugin as a real package; no-network guard
tests/test_effort.py   thresholds, invalid scores, clamp semantics
tests/test_jev_client.py   fake transport only: schema, truncation, timeout, no-key
tests/test_middleware.py   21 cases: opt-in, mutate-once, reuse, isolation, bounds
tests/test_plugin_registration.py  manifest + register() contract
```

## Tests

```
PYTHONPATH=/usr/local/lib/hermes-agent \
  /root/workspace/Hermes/hermes-loops/.venv/bin/python -m pytest tests -q
→ 35 passed in 0.15s
```

Fake transport / fake classifier throughout; `socket.socket` and
`socket.create_connection` are monkeypatched to raise in `test_no_network_*` and
the three `no_network`-fixtures cases, so a unit run provably opens no socket.
The canonical repo runner (`scripts/run_tests.sh`) expects a plugin inside the
tree (`tests/plugins/…`) and was not used for this out-of-tree scaffold.

## Not covered in Task 1 (next tasks, per plan)

- Loading the plugin through Hermes' real discovery (`plugins/plugin_loader.py`:
  parents first, then siblings as `<name>.<stem>`, which is why relative imports
  here work) — untested; the manifest and `register()` are tested against a fake ctx.
- Integration: `TestDriver().run_agent()` end-to-end proof that `middleware_trace`
  appears on the wire (Task 5's acceptance criterion).
- Payload privacy/latency instrumentation (Jev call count + p50/p95, turn latency
  delta) — plan §"Recover/measure"; only mode/outcome/elapsed are logged today.
- Consent/discovery UX (`reasoning_effort auto` discovery message, FALC copy) — plan Task 4.

## Open questions for the next worker

1. Session-scoped memoisation means one decision per session, not per model. If the
   model changes mid-session (`/model`), should the decision be invalidated? Plan §2.2.
2. `recommend` mode currently returns a replacement payload with unchanged content
   (so a trace entry exists). If `recommend` should leave no trace at all, return `None`
   instead — one line, plus one test.
3. Endpoint is configurable (`settings.endpoint`); should it be fixed to TypeSafe
   only, to keep a single documented disclosure surface?
