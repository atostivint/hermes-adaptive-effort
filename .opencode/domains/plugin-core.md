# Domain: plugin-core

Routing modes, decision memory, request rewriting and the `/hae` command - the heart of the plugin.

## Owns
- `__init__.py` - `register(ctx)`: `llm_request` middleware, session lifecycle hooks
  (`on_session_end`, `on_session_finalize`, `on_session_reset`, `subagent_start`, `subagent_stop`,
  `post_api_request`, `api_request_error`) and the `/hae` command.
- `middleware.py` - settings (`DEFAULTS`), mode resolution, decision cache, `_effort_slot` injection
  sites, route re-clamp, applied-change feed, `reset_state`.
- `effort.py` - pure mapping: route choice/score -> wire effort. Stdlib only, lazy `agent.*` imports.
- `goal_loop.py` - local markers for the host's `/goal` and kanban goal-continuation turns (stdlib
  only; compares the start of the latest user text and never stores it).
- `command.py` - `/hae` help/status/probe/mode verbs, `_ENTRY_FIELDS` (the only rendered session
  allowlist), status schema `hermes-adaptive-effort.status.v2`.
- `plugin.yaml` - manifest, settings schema and defaults (mirrored by `middleware.DEFAULTS`).

## Invariants (do not break; full text in AGENTS.md)
- Default mode is `off` for parent and subagent. Public modes: `auto`, `once`, `always`, `off`.
- Fail-open everywhere: `on_llm_request` catches all; an error leaves the request untouched and
  records `failed`.
- Never add a thinking toggle; never overwrite malformed/disabled controls; never touch `"none"` or
  `enabled: false`.
- Reason codes are stable and additive (`model_missing`, `scorer_paused`, ...). The status contract is
  v2; the probe and the change feed stay v1.
- Precedence: per-conversation override > in-process `/hae` override > config `settings.mode`.
- At most one effort scorer call per turn, plus one optional continuation call. Bounded by `max_turns`.
- `respect_manual_effort` (default `true`) stands a conversation down after an operator-chosen effort:
  no scorer call, no rewrite, an `off`-shaped entry with `manual_effort: true`, until a
  per-conversation mode set or a session reset. Memory is values only, in process.
- `effort_floor` / `effort_ceiling` clamp the scorer label after the answer; the ceiling also caps
  `tool_error_escalation` and `goal_continuation_escalation`.
- `goal_continuation_escalation` (default `false`) is scoped to exact dynamic routes with named
  choices, `always`/`auto` only; the marker is not proof of a real failure (the host emits it when its
  goal judge fails open).

## Known traps
- `middleware.py`, `effort.py`, `command.py` and `plugin.yaml` are all serialization points: one task
  at a time on each.
- `plugin.yaml` defaults and `middleware.DEFAULTS` must agree - a test (`test_config_schema.py`) reads both.
- A rewrite is recorded once, at the single return point, or the change feed replays and lies.
- Do not reorder imports: ruff's `I` rules are deliberately off.

## Proof
- `.\scripts\run_tests.ps1 -q` (whole suite) plus a named selector, e.g.
  `.\scripts\run_tests.ps1 -q -k middleware`.
- `.\scripts\run_lint.ps1`.
- For a mode or injection change: the exact field path touched, the before/after value, and the test
  that pins it (`test_middleware.py`, `test_command.py`, `test_command_modes.py`).

## Open threads
- Anthropic per-message continuity inside a tool loop (marker not implemented; escalation excluded).
- Behaviour on unlisted routes depends on the trial verdict store (`route-trial`).
