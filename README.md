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

Bounded state: `OrderedDict` keyed by `session_id`, FIFO-capped
(`max_sessions`, default 64), cleared by `on_session_end`. Entries store only
`state`/`label`/`target` — **no prompt text is ever persisted** (asserted by
`test_no_prompt_text_is_stored`), and reason strings carry effort values only.

## Files

```
plugin.yaml            manifest: kind standalone, capability llm_request
__init__.py            register(ctx) -> middleware + on_session_end
effort.py              rubric -> label -> clamped wire value (pure)
jev_client.py          JevClient: bounded, validated, fail-open, injectable transport
middleware.py          on_llm_request + session state + fail-open table above
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
