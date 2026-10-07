#!/usr/bin/env python3
"""Run one bounded Codex/Responses effort-compatibility probe through Hermes.

The caller owns the Codex usage-meter checks before and after this command.
This process injects a deterministic local score into the enabled adaptive
effort plugin, observes the final HTTPX request, and permits at most one
physical provider send. It never stores a prompt, response body, header, or key.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import faulthandler
import importlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


HERMES_PLUGIN_DIR = "hermes-adaptive-effort"
CODEX_HOST = "chatgpt.com"
CODEX_PATH = "/backend-api/codex/responses"
CODEX_MODELS_PATH = "/backend-api/codex/models"
MAX_OUTPUT_TOKENS = 256
SYNTHETIC_PROMPT = "Reply with exactly the single word OK. Do not use tools."


class _ProbeGate(Exception):
    """A request was stopped before the network boundary."""


def _replace_hard_exit(exit_code: int) -> None:
    raise SystemExit(exit_code)


def _guard_socket_call(function: Any, network_permission: Any) -> Any:
    def guarded(sock: Any, address: Any) -> Any:
        if not getattr(network_permission, "active", False):
            raise OSError("probe_blocked_unobserved_socket_connect")
        return function(sock, address)

    return guarded


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _publish_trace(state: dict[str, Any], event: str) -> None:
    path = state.get("trace_path")
    if not path:
        return
    payload = {
        "event": event,
        "at": _utc_now(),
        "plugin_ready": state.get("plugin_ready") is True,
        "classifier_calls": state.get("classifier_calls", 0),
        "transport_attempts": state.get("transport_attempts", 0),
        "physical_send_attempts": state.get("send_attempts", 0),
        "observed_model": state.get("observed_model"),
        "observed_effort": state.get("observed_effort"),
        "middleware_changed": state.get("middleware_changed"),
        "middleware_input_reasoning": _safe_effort(state.get("middleware_input_reasoning")),
        "middleware_output_reasoning": _safe_effort(state.get("middleware_output_reasoning")),
        "http_status": state.get("http_status"),
        "response_complete": state.get("response_complete") is True,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _score_to_effort(score: int) -> str:
    return ("low", "medium", "high")[score]


def _safe_effort(value: Any) -> str | None:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
    return value if isinstance(value, str) and value in allowed else None


def _normalize_cli_failure(output: str, exit_code: int) -> str | None:
    if exit_code == 0:
        return None
    lowered = output.lower()
    if re.search(r"\b(401|403)\b|unauthori[sz]ed|forbidden", lowered):
        return "codex_auth_or_access_failure"
    if re.search(r"\b429\b|rate.?limit|quota exceeded", lowered):
        return "codex_rate_limited"
    if re.search(r"model.{0,40}(not found|unavailable|unsupported)", lowered):
        return "codex_model_unavailable"
    if "provider" in lowered and any(word in lowered for word in ("resolve", "unavailable", "failed")):
        return "provider_resolution_failure"
    if "error" in lowered or "failed" in lowered:
        return "hermes_cli_error"
    return "hermes_turn_incomplete"


def _load_plugin_middleware(
    manager: Any, state: dict[str, Any], score: int, classify: bool
) -> None:
    """Set the loaded plugin's in-memory mode without changing config."""
    matches = []
    for loaded in getattr(manager, "_plugins", {}).values():
        manifest = getattr(loaded, "manifest", None)
        path = Path(str(getattr(manifest, "path", "")))
        if path.name.casefold() == HERMES_PLUGIN_DIR and loaded.enabled and loaded.module:
            matches.append((path.resolve(), loaded.module))
    if len(matches) != 1:
        state["plugin_ready"] = False
        state["plugin_failure"] = "adaptive_plugin_not_uniquely_loaded"
        _publish_trace(state, "plugin_not_ready")
        return

    plugin_path, plugin_module = matches[0]
    middleware_module = None
    package = getattr(plugin_module, "__package__", "")
    if package:
        with contextlib.suppress(ImportError, ValueError):
            middleware_module = importlib.import_module(package + ".middleware")
    if middleware_module is None:
        expected = (plugin_path / "middleware.py").resolve()
        for candidate in tuple(sys.modules.values()):
            candidate_file = getattr(candidate, "__file__", None)
            if candidate_file and Path(candidate_file).resolve() == expected:
                middleware_module = candidate
                break
    if middleware_module is None:
        state["plugin_ready"] = False
        state["plugin_failure"] = "adaptive_middleware_module_not_found"
        _publish_trace(state, "middleware_not_found")
        return

    if classify:
        class _FixedScore:
            def classify(self, _prompt: str) -> int:
                state["classifier_calls"] += 1
                return score

        middleware_module._MODE_OVERRIDE = "always"
        middleware_module._classifier_factory = lambda timeout=None: _FixedScore()
        state["classifier_mode"] = "controlled_score"
    else:
        class _DisabledScore:
            def classify(self, _prompt: str) -> int:
                state["classifier_calls"] += 1
                raise RuntimeError("classifier_disabled_for_availability")

        middleware_module._MODE_OVERRIDE = "off"
        middleware_module._classifier_factory = lambda timeout=None: _DisabledScore()
        state["classifier_mode"] = "off"
    state["plugin_ready"] = True
    state["plugin_path"] = str(plugin_path)
    _publish_trace(state, "deterministic_classifier_installed" if classify
                   else "plugin_disabled_in_memory")


def _install_plugin_override(
    score: int, state: dict[str, Any], classify: bool
) -> tuple[Any, Any, Any, Any]:
    from hermes_cli.plugins import PluginManager
    import hermes_cli.middleware as middleware_host

    original = PluginManager.discover_and_load
    original_apply = middleware_host.apply_llm_request_middleware

    def observed_apply(request: dict[str, Any], **context: Any) -> Any:
        result = original_apply(request, **context)
        if context.get("provider") == "openai-codex":
            before = request.get("reasoning")
            after = result.payload.get("reasoning") if isinstance(result.payload, dict) else None
            state["middleware_input_reasoning"] = (
                _safe_effort(before.get("effort")) if isinstance(before, dict) else None)
            state["middleware_output_reasoning"] = (
                _safe_effort(after.get("effort")) if isinstance(after, dict) else None)
            state["middleware_input_reasoning_effort"] = _safe_effort(
                request.get("reasoning_effort"))
            state["middleware_output_reasoning_effort"] = (
                _safe_effort(result.payload.get("reasoning_effort"))
                if isinstance(result.payload, dict) else None)
            state["middleware_changed"] = bool(result.changed)
            _publish_trace(state, "adaptive_middleware_applied")
        return result

    def discover_and_load(manager: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(manager, *args, **kwargs)
        _load_plugin_middleware(manager, state, score, classify)
        return result

    PluginManager.discover_and_load = discover_and_load
    middleware_host.apply_llm_request_middleware = observed_apply
    return PluginManager, original, middleware_host, original_apply


def _install_single_attempt_limit(state: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any, Any]:
    """Allow one outer attempt and disable retries, provider fallback, and auto-recovery."""
    import run_agent
    import hermes_cli.oneshot as oneshot
    from agent import turn_recovery_autorecover

    agent_class = run_agent.AIAgent
    original_init = agent_class.__init__
    original_fallback = oneshot.get_fallback_chain
    original_recovery = turn_recovery_autorecover.auto_recover_after_exhaustion

    def initialize(agent: Any, *args: Any, **kwargs: Any) -> None:
        original_init(agent, *args, **kwargs)
        # Hermes' retry loop uses ``retry_count < max_retries``; one means the
        # initial request is allowed and no outer retry is allowed afterward.
        agent._api_max_retries = 1
        state["outer_retries_disabled"] = True

    agent_class.__init__ = initialize
    oneshot.get_fallback_chain = lambda _config: None
    turn_recovery_autorecover.auto_recover_after_exhaustion = lambda *a, **kw: None
    state["fallback_and_outer_recovery_disabled"] = True
    return (agent_class, original_init, oneshot, original_fallback,
            turn_recovery_autorecover, original_recovery)


def _update_request(httpx: Any, request: Any, state: dict[str, Any],
                    expected_model: str, expected_effort: str | None, live: bool,
                    availability_only: bool) -> Any:
    parts = urlsplit(str(request.url))
    if (parts.scheme != "https" or parts.hostname != CODEX_HOST
            or parts.port not in (None, 443) or parts.path != CODEX_PATH
            or parts.query or request.method.upper() != "POST"):
        state["blocked_non_target"] += 1
        state["blocked_host"] = parts.hostname
        state["blocked_path"] = parts.path
        _publish_trace(state, "non_target_request_blocked")
        raise _ProbeGate("non_target_transport_blocked")
    if state["transport_attempts"]:
        state["blocked_repeat"] += 1
        raise _ProbeGate("second_physical_send_blocked")
    state["transport_attempts"] += 1
    if not state["plugin_ready"]:
        raise _ProbeGate(state.get("plugin_failure", "adaptive_plugin_not_ready"))

    try:
        body = json.loads(request.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        raise _ProbeGate("request_body_not_json") from None
    if not isinstance(body, dict):
        raise _ProbeGate("request_body_invalid")
    reasoning = body.get("reasoning")
    observed_model = body.get("model")
    if isinstance(reasoning, dict) and "effort" in reasoning:
        effort_field = "reasoning.effort"
        observed_effort = reasoning.get("effort")
    elif "reasoning_effort" in body:
        effort_field = "reasoning_effort"
        observed_effort = body.get("reasoning_effort")
    else:
        effort_field = None
        observed_effort = None
    if observed_model != expected_model:
        state["observed_model"] = observed_model if isinstance(observed_model, str) else None
        raise _ProbeGate("wire_model_mismatch")
    if not availability_only and observed_effort != expected_effort:
        state["observed_model"] = observed_model
        state["observed_effort"] = observed_effort
        state["failure_code"] = "wire_effort_mismatch"
        raise _ProbeGate("wire_effort_mismatch")

    old_limit = body.get("max_output_tokens")
    if isinstance(old_limit, bool) or (old_limit is not None and not isinstance(old_limit, int)):
        raise _ProbeGate("output_limit_invalid")
    if old_limit is not None and old_limit < 1:
        raise _ProbeGate("output_limit_invalid")
    body["max_output_tokens"] = min(old_limit, MAX_OUTPUT_TOKENS) if old_limit else MAX_OUTPUT_TOKENS
    tools = body.pop("tools", [])
    if not isinstance(tools, list):
        raise _ProbeGate("tools_field_invalid")
    body.pop("tool_choice", None)
    body.pop("parallel_tool_calls", None)
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 65536:
        raise _ProbeGate("request_body_too_large")
    headers = [(key, value) for key, value in request.headers.multi_items()
               if key.lower() not in {"content-length", "transfer-encoding"}]
    bounded = httpx.Request(request.method, request.url, headers=headers,
                            content=encoded, extensions=request.extensions)
    state["observed_model"] = observed_model
    state["observed_effort"] = observed_effort
    state["effort_field"] = effort_field
    state["output_token_cap"] = body["max_output_tokens"]
    state["tool_count_removed"] = len(tools)
    _publish_trace(state, "wire_effort_verified_before_send")
    if not live:
        raise _ProbeGate("dry_run_blocked_before_send")
    state["send_attempts"] += 1
    _publish_trace(state, "physical_send_started")
    return bounded


def _install_transport_guard(expected_model: str, expected_effort: str | None,
                             live: bool, state: dict[str, Any],
                             availability_only: bool) -> tuple[Any, ...]:
    import httpx
    import socket
    import threading

    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send
    original_iter_bytes = httpx.Response.iter_bytes
    original_connect = getattr(socket.socket, "connect", None)
    original_connect_ex = getattr(socket.socket, "connect_ex", None)
    network_permission = threading.local()
    guarded_connect = (_guard_socket_call(original_connect, network_permission)
                       if callable(original_connect) else None)
    guarded_connect_ex = (_guard_socket_call(original_connect_ex, network_permission)
                          if callable(original_connect_ex) else None)

    def observed_send(function: Any, client: Any, request: Any,
                      *args: Any, **kwargs: Any) -> Any:
        previous = getattr(network_permission, "active", False)
        network_permission.active = True
        kwargs["follow_redirects"] = False
        try:
            return function(client, request, *args, **kwargs)
        finally:
            network_permission.active = previous

    def guarded_send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        parts = urlsplit(str(request.url))
        if parts.hostname == CODEX_HOST and parts.path == CODEX_MODELS_PATH:
            state["catalog_request_facts"] = {
                "scheme": parts.scheme,
                "port": parts.port,
                "query_present": bool(parts.query),
                "method": request.method.upper(),
            }
        if (parts.scheme == "https" and parts.hostname == CODEX_HOST
                and parts.port in (None, 443) and parts.path == CODEX_MODELS_PATH
                and request.method.upper() == "GET"):
            if state["catalog_requests"]:
                state["blocked_repeat"] += 1
                raise _ProbeGate("duplicate_catalog_request_blocked")
            state["catalog_requests"] += 1
            _publish_trace(state, "authenticated_catalog_read")
            response = observed_send(original_send, client, request, *args, **kwargs)
            state["catalog_http_status"] = response.status_code
            _publish_trace(state, "authenticated_catalog_response")
            return response
        bounded = _update_request(httpx, request, state, expected_model, expected_effort,
                                  live, availability_only)
        try:
            response = observed_send(original_send, client, bounded, *args, **kwargs)
        except Exception as exc:
            state["transport_error"] = type(exc).__name__
            raise
        state["http_status"] = response.status_code
        state["_target_response"] = response
        _publish_trace(state, "provider_response_headers_received")
        return response

    async def block_async_send(_client: Any, _request: Any,
                               *args: Any, **kwargs: Any) -> Any:
        state["blocked_async_transport"] = True
        raise _ProbeGate("unobserved_async_transport_blocked")

    def observed_iter_bytes(response: Any, *args: Any, **kwargs: Any):
        target = response is state.get("_target_response")
        buffer = b""
        event_name = ""
        data_lines: list[bytes] = []
        for chunk in original_iter_bytes(response, *args, **kwargs):
            if target:
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.rstrip(b"\r")
                    if not line:
                        if data_lines:
                            _observe_sse_frame(b"\n".join(data_lines), event_name, state)
                        event_name, data_lines = "", []
                    elif line.startswith(b"event:"):
                        event_name = line[6:].strip().decode("ascii", errors="ignore")
                    elif line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip())
            yield chunk
        if target and data_lines:
            _observe_sse_frame(b"\n".join(data_lines), event_name, state)

    httpx.Client.send = guarded_send
    httpx.AsyncClient.send = block_async_send
    httpx.Response.iter_bytes = observed_iter_bytes
    if guarded_connect is not None:
        socket.socket.connect = guarded_connect
    if guarded_connect_ex is not None:
        socket.socket.connect_ex = guarded_connect_ex
    state["socket_guard_active"] = guarded_connect is not None and guarded_connect_ex is not None
    return (httpx, original_send, original_async_send, original_iter_bytes,
            socket, original_connect, original_connect_ex)


def _observe_sse_frame(raw: bytes, event_name: str, state: dict[str, Any]) -> None:
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    kind = event.get("type") if isinstance(event, dict) else None
    kind = kind or event_name
    if kind == "response.completed":
        response = event.get("response") if isinstance(event, dict) else None
        if isinstance(response, dict) and response.get("status") == "completed":
            state["response_complete"] = True
            usage = response.get("usage")
            if isinstance(usage, dict):
                state["input_tokens"] = usage.get("input_tokens")
                state["output_tokens"] = usage.get("output_tokens")
            _publish_trace(state, "completed_event_observed")
    elif kind in {"response.failed", "response.incomplete"}:
        state["response_terminal_failure"] = kind


def run_probe(model: str, score: int, initial_effort: str, *, live: bool,
              hermes_source: str, hermes_home: str,
              trace_path: str | None = None,
              quota_before_percent: int | None = None,
              quota_baseline_percent: int = 31,
              availability_only: bool = False) -> dict[str, Any]:
    if score not in (0, 1, 2):
        raise ValueError("score_must_be_0_1_or_2")
    expected_effort = None if availability_only else _score_to_effort(score)
    if not availability_only and initial_effort == expected_effort:
        raise ValueError("initial_effort_must_differ_from_target")
    stop_percent = min(100, quota_baseline_percent + 30) - 1
    if live and (quota_before_percent is None or quota_before_percent >= stop_percent):
        raise ValueError("codex_quota_preventive_stop")

    sys.path.insert(0, hermes_source)
    os.environ["HERMES_HOME"] = hermes_home
    state: dict[str, Any] = {
        "plugin_ready": False, "classifier_calls": 0, "send_attempts": 0,
        "transport_attempts": 0, "catalog_requests": 0,
        "blocked_repeat": 0, "blocked_non_target": 0, "response_complete": False,
        "response_terminal_failure": None, "http_status": None,
        "input_tokens": None, "output_tokens": None, "trace_path": trace_path,
    }
    manager_class = original_discover = None
    middleware_host = original_apply = None
    agent_class = original_agent_init = None
    oneshot_module = original_fallback = None
    recovery_module = original_recovery = None
    httpx = original_send = original_async_send = original_iter = None
    socket_module = original_connect = original_connect_ex = None
    original_os_exit = os._exit
    os._exit = _replace_hard_exit
    code = 1
    stdout = io.StringIO()
    stack_stream = None
    if trace_path:
        stack_stream = Path(trace_path).with_suffix(".stacks.txt").open("w", encoding="utf-8")
        faulthandler.dump_traceback_later(15, repeat=False, file=stack_stream)
    try:
        _publish_trace(state, "before_hermes_import")
        import hermes_cli.main as hermes_main
        _publish_trace(state, "hermes_imported")

        (manager_class, original_discover,
         middleware_host, original_apply) = _install_plugin_override(
             score, state, not availability_only)
        (agent_class, original_agent_init, oneshot_module, original_fallback,
         recovery_module, original_recovery) = _install_single_attempt_limit(state)
        (httpx, original_send, original_async_send, original_iter,
         socket_module, original_connect, original_connect_ex) = _install_transport_guard(
            model, expected_effort, live, state, availability_only)
        sys.argv = ["hermes", "--model", model, "--provider", "openai-codex",
                    "--reasoning", initial_effort, "--toolsets", "", "--ignore-rules",
                    "-z", SYNTHETIC_PROMPT]
        _publish_trace(state, "before_hermes_main")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            try:
                result = hermes_main.main()
                code = result if isinstance(result, int) else 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except _ProbeGate as exc:
        state["blocked_reason"] = str(exc)
    except Exception as exc:
        state["runtime_error"] = type(exc).__name__
    finally:
        os._exit = original_os_exit
        if stack_stream is not None:
            faulthandler.cancel_dump_traceback_later()
            stack_stream.close()
        if manager_class is not None and original_discover is not None:
            manager_class.discover_and_load = original_discover
        if middleware_host is not None and original_apply is not None:
            middleware_host.apply_llm_request_middleware = original_apply
        if agent_class is not None and original_agent_init is not None:
            agent_class.__init__ = original_agent_init
        if oneshot_module is not None and original_fallback is not None:
            oneshot_module.get_fallback_chain = original_fallback
        if recovery_module is not None and original_recovery is not None:
            recovery_module.auto_recover_after_exhaustion = original_recovery
        if (httpx is not None and original_send is not None
                and original_async_send is not None and original_iter is not None):
            httpx.Client.send = original_send
            httpx.AsyncClient.send = original_async_send
            httpx.Response.iter_bytes = original_iter
        if socket_module is not None:
            if original_connect is not None:
                socket_module.socket.connect = original_connect
            if original_connect_ex is not None:
                socket_module.socket.connect_ex = original_connect_ex

    output = stdout.getvalue()
    return {
        "schema": "hermes-adaptive-effort.codex-probe.v1",
        "created_at": _utc_now(), "model": model,
        "score": None if availability_only else score,
        "target_effort": expected_effort,
        "availability_only": availability_only,
        "initial_effort": initial_effort,
        "observed_model": state.get("observed_model"),
        "observed_effort_field": state.get("effort_field"),
        "observed_effort": state.get("observed_effort"),
        "middleware_changed": state.get("middleware_changed"),
        "middleware_input_reasoning": _safe_effort(state.get("middleware_input_reasoning")),
        "middleware_output_reasoning": _safe_effort(state.get("middleware_output_reasoning")),
        "middleware_input_reasoning_effort": _safe_effort(
            state.get("middleware_input_reasoning_effort")),
        "middleware_output_reasoning_effort": _safe_effort(
            state.get("middleware_output_reasoning_effort")),
        "output_token_cap": state.get("output_token_cap"),
        "tool_count_removed": state.get("tool_count_removed"),
        "http_status": state.get("http_status"),
        "response_complete": state.get("response_complete") is True,
        "synthetic_answer_match": bool(re.fullmatch(r"\s*OK\s*", output)),
        "input_tokens": state.get("input_tokens"),
        "output_tokens": state.get("output_tokens"),
        "classifier_calls": state.get("classifier_calls", 0),
        "physical_send_attempts": state.get("send_attempts", 0),
        "blocked_repeat_attempts": state.get("blocked_repeat", 0),
        "blocked_non_target_attempts": state.get("blocked_non_target", 0),
        "blocked_host": state.get("blocked_host"),
        "blocked_path": state.get("blocked_path"),
        "catalog_requests": state.get("catalog_requests", 0),
        "catalog_http_status": state.get("catalog_http_status"),
        "catalog_request_facts": state.get("catalog_request_facts"),
        "hermes_exit_code": code,
        "plugin_loaded": state.get("plugin_ready") is True,
        "failure_code": state.get("failure_code") or state.get("blocked_reason") or state.get("runtime_error")
            or state.get("response_terminal_failure") or _normalize_cli_failure(output, code),
        "cli_output_chars": len(output),
        "dry_run": not live,
        "quota_window_minutes": 300,
        "quota_baseline_percent": quota_baseline_percent,
        "quota_stop_percent": stop_percent,
        "quota_hard_ceiling_percent": min(100, quota_baseline_percent + 30),
        "quota_before_percent": quota_before_percent,
        "quota_after_percent": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--score", required=True, type=int, choices=(0, 1, 2))
    parser.add_argument("--initial-effort", required=True,
                        choices=("minimal", "low", "medium", "high", "xhigh", "max", "ultra"))
    parser.add_argument("--hermes-source", required=True)
    parser.add_argument("--hermes-home", required=True)
    parser.add_argument("--trace-file")
    parser.add_argument("--result-file")
    parser.add_argument("--quota-before-percent", type=int)
    parser.add_argument("--quota-baseline-percent", type=int, default=31)
    parser.add_argument("--availability-only", action="store_true",
                        help="turn the plugin off in memory and test normal Hermes model access")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and validate the real Hermes request, then block before network")
    args = parser.parse_args(argv)
    try:
        result = run_probe(args.model, args.score, args.initial_effort,
                           live=not args.dry_run, hermes_source=args.hermes_source,
                           hermes_home=args.hermes_home, trace_path=args.trace_file,
                           quota_before_percent=args.quota_before_percent,
                           quota_baseline_percent=args.quota_baseline_percent,
                           availability_only=args.availability_only)
    except ValueError as exc:
        print(json.dumps({"state": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    if args.result_file:
        result_path = Path(args.result_file)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = result_path.with_suffix(result_path.suffix + ".tmp")
        temporary.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
        temporary.replace(result_path)
    if args.availability_only:
        passed = (result["plugin_loaded"] and result["classifier_calls"] == 0
                  and result["middleware_changed"] is False
                  and result["observed_model"] == args.model
                  and result["physical_send_attempts"] == (0 if args.dry_run else 1)
                  and (args.dry_run or (result["response_complete"]
                                        and result["hermes_exit_code"] == 0)))
    else:
        passed = (result["plugin_loaded"] and result["classifier_calls"] == 1
                  and result["observed_model"] == args.model
                  and result["observed_effort_field"] == "reasoning.effort"
                  and result["observed_effort"] == result["target_effort"]
                  and result["physical_send_attempts"] == (0 if args.dry_run else 1)
                  and (args.dry_run or (result["response_complete"]
                                        and result["hermes_exit_code"] == 0)))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
