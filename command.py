"""/hae — explain, inspect and steer the plugin.

``status`` and ``probe`` never classify a conversation, never rewrite a request
and never store anything: they only render what the middleware already holds.
``auto|once|always|off`` set the mode for FUTURE requests of this
process — the plugin never edits the operator's config file, and the reply says
so. Every command runs through the plugin dispatcher's argument split, so
`handle()` receives a string, not a list.

Schemas below are part of the documented contract (`hermes-adaptive-effort.*.v1`): the keys
are stable, and no prompt text ever leaves this module beyond the text the
operator typed themselves for a probe.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import middleware as _middleware
from . import history_store as _history_store
from . import scorers as _scorers

PLUGIN_ID = _middleware.PLUGIN_ID
MODES = _middleware.VALID_MODES
USAGE = """Usage:
  /hae                                      Show this conversation's effort history
  /hae history [N|all] [full]               Show recent applied effort changes
  /hae status                               Show a concise status for this conversation
  /hae status full                          Show detailed status for this conversation
  /hae status json                          Machine-readable status payload
  /hae auto|once|always|off                  Set the mode used by future requests
  /hae probe <text>                         Classify <text> once (prints score/label, stores nothing)
  /hae help                                 Show this help

History:
  The default view shows 10 applied changes. N is 1–64; all shows retained changes.
  Add full to include provider, API route, score, scorer and timing details.
  History is scoped to the active conversation and contains no prompt text.

Modes:
  auto        classify each message on verified dynamic routes; otherwise keep
              one decision per model and route for this conversation
  once        keep one decision per model and route for this conversation
  always      classify each new user message
  off         do not score or change requests; route stays visible in Desktop

A mode set here applies to future requests served by this process. It is not
written to config.yaml (nothing here edits your files), so it does not survive a
restart; persist it as plugins.entries.hermes-adaptive-effort.settings.mode instead.
Probe asks the configured scorer for a rubric score on the text you typed, plus any optional
configured classifier guidance — it does not use or store your session's conversation, and never
writes to session state."""

STATUS_SCHEMA = "hermes-adaptive-effort.status.v2"
PROBE_SCHEMA = "hermes-adaptive-effort.probe.v1"

#: The only session fields ever rendered — an entry may hold anything (it is
#: plugin-author payload), so prompt text and provider junk are filtered out.
_ENTRY_FIELDS = (
    "conversation_id", "state", "score", "label", "target", "decision_type", "choices",
    "cache_behavior", "mode", "provider", "model", "api_mode",
    "scorer_provider", "scorer_model",
    "requests", "probes", "elapsed_ms", "failure", "updated_at",
)


def handle(raw_args: str) -> str:
    """Dispatcher entry point. Signature must match a PluginCommand's handler."""
    try:
        return _dispatch(raw_args)
    except Exception:
        # Output rules are a contract: even on a rendering bug we return usage
        # rather than letting an exception bubble into the chat.
        return USAGE


def _dispatch(raw_args: str) -> str:
    parts = (raw_args or "").split()
    if not parts:
        return _history_text(10)
    verb, *rest = parts
    if verb in {"help", "-h", "--help"}:
        return USAGE
    if verb in MODES:
        if rest:
            return USAGE
        return _set_mode(verb)
    if verb == "status":
        if not rest:
            return _status_text()
        if rest == ["json"]:
            return _status_json()
        if rest == ["full"]:
            return _status_full_text()
        return USAGE
    if verb == "history":
        parsed = _parse_history_args(rest)
        return _history_text(*parsed) if parsed is not None else USAGE
    if verb == "probe":
        if not rest:
            return USAGE
        return _probe(" ".join(rest))
    return USAGE


# ── mode ────────────────────────────────────────────────────────────────────

def _set_mode(target: str) -> str:
    """Set the mode for future requests of this process, and say exactly that.

    The override lives in memory: writing `config.yaml` from a chat command would
    let the plugin change a file the operator owns, silently and without
    confirmation, which is not this command's call. The reply therefore names the
    scope (future requests, this process) and the persist path, and it never
    repeats a credential or any configuration value beyond the two modes.
    """
    before = _middleware._settings()["mode"]
    applied = _middleware.set_mode_override(target)
    if applied is None:  # unreachable from _dispatch; kept fail-safe
        return USAGE
    if applied == before:
        return (f"hermes-adaptive-effort mode: {applied} (unchanged; applies to future requests "
                f"in this process, not persisted)")
    return (f"hermes-adaptive-effort mode: {before} -> {applied} (applies to future requests in "
            f"this process; not persisted, set "
            f"plugins.entries.{PLUGIN_ID}.settings.mode to make it stick)")


# ── status ──────────────────────────────────────────────────────────────────

def _public_entry(session_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"session_id": str(session_id)}
    for key in _ENTRY_FIELDS:
        out[key] = entry.get(key)
    return out


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _last_session(sessions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Most recently updated entry; ties go to the last one in the list."""
    best: Optional[Dict[str, Any]] = None
    best_key = (-1.0, -1)
    for index, entry in enumerate(sessions):
        key = (_number(entry.get("updated_at")), index)
        if key >= best_key:
            best_key, best = key, entry
    return best


def _conversation_state(limit: int = 10) -> Dict[str, Any]:
    """Return the active conversation's persistent view, with a local-memory fallback."""
    _scope, identities = _history_store.current_identity()
    if not identities:
        return {"available": True, "scope_id": None, "events": [],
                "latest": None, "status": {}, "identity_missing": True}
    view = _history_store.read_history(identities, limit=limit)
    if view.get("status"):
        return view

    matching = []
    wanted = set(identities)
    for key, entry in _middleware.session_state().items():
        conversation = str(entry.get("conversation_id") or "")
        if conversation in wanted or str(key) in wanted:
            matching.append(_public_entry(key, entry))
    latest = _last_session(matching)
    if latest:
        view["status"] = latest
    return view


def _parse_history_args(args: List[str]) -> Optional[tuple[int, bool]]:
    """Parse the small explicit history grammar without accepting stray values."""
    if not args:
        return 10, False
    if args == ["full"]:
        return 10, True
    if len(args) not in (1, 2) or (len(args) == 2 and args[1] != "full"):
        return None
    count = args[0]
    if count == "all":
        return _history_store.MAX_HISTORY_CHANGES, len(args) == 2
    if not count.isdigit():
        return None
    parsed = int(count)
    if not 1 <= parsed <= _history_store.MAX_HISTORY_CHANGES:
        return None
    return parsed, len(args) == 2


def _cache_label(verdict: Any) -> str:
    return {
        "compatible": "Compatible",
        "sensitive": "Sensitive",
        "not_verified": "Not verified",
    }.get(str(verdict or ""), "Not verified")


def _local_time(at: Any, *, full: bool = False) -> str:
    try:
        value = datetime.fromtimestamp(float(at)).astimezone()
        return value.strftime("%Y-%m-%d %H:%M:%S %z" if full else "%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


def _model_name(value: Any) -> str:
    model = str(value or "")
    if not model:
        return "unknown"
    return model if len(model) <= 28 else model[:25] + "..."


def _render_history_event(event: Dict[str, Any], *, full: bool = False) -> List[str]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    model = str(event.get("model") or details.get("model") or "unknown")
    transition = f"{event.get('from', '?')} -> {event.get('to', '?')}"
    cache = _cache_label(event.get("cache_verdict"))
    if not full:
        return [f"{_local_time(event.get('at')):<17} {_model_name(model):<28} "
                f"{transition:<15} {cache}"]
    lines = [f"{_local_time(event.get('at'), full=True)}  {model}",
             f"  effort: {transition}",
             f"  cache: {cache}",
             f"  provider: {event.get('provider') or details.get('provider') or 'unknown'}",
             f"  API route: {event.get('api_mode') or details.get('api_mode') or 'unknown'}"]
    for field, label in (("score", "score"), ("label", "rubric label"),
                         ("target", "route target"), ("scorer_provider", "scorer"),
                         ("scorer_model", "scorer model"), ("elapsed_ms", "scoring time"),
                         ("failure", "result")):
        value = details.get(field)
        if value is not None:
            suffix = " ms" if field == "elapsed_ms" else ""
            lines.append(f"  {label}: {value}{suffix}")
    return lines


def _history_text(limit: int = 10, full: bool = False) -> str:
    view = _conversation_state(limit)
    lines = ["Hermes Adaptive Effort — recent changes"]
    if view.get("identity_missing"):
        return "\n".join(lines + ["No active conversation identity is available in this command context."])
    if not view.get("available"):
        return "\n".join(lines + ["History storage is unavailable. Request routing is unaffected."])
    events = view.get("events") or []
    mode = (view.get("status") or {}).get("mode") or _middleware._settings()["mode"]
    lines.append(f"Current conversation · mode: {mode} · {len(events)} shown")
    lines.append("Time zone: " + datetime.now().astimezone().strftime("%z"))
    if not events:
        lines.append("No applied effort changes recorded yet.")
    elif full:
        for index, event in enumerate(events):
            if index:
                lines.append("")
            lines.extend(_render_history_event(event, full=True))
    else:
        lines.append("")
        lines.append(f"{'Time':<17} {'Model':<28} {'Effort':<15} Cache")
        lines.extend(_render_history_event(event)[0] for event in events)
    lines.append("")
    lines.append("/hae status · /hae history all · /hae history full · /hae help")
    return "\n".join(lines)


def _status_payload() -> Dict[str, Any]:
    settings = _middleware._settings()
    public_settings = dict(settings)
    # Operator guidance is sent only to the selected scorer for a classification;
    # never return its contents through status, Desktop polling, or CLI JSON.
    configured_guidance = public_settings.pop("classification_instructions", "")
    public_settings["classification_instructions_configured"] = bool(configured_guidance)
    public_settings["classification_instructions_chars"] = len(configured_guidance)
    # A custom endpoint can carry harmless routing parameters (or, despite our
    # guidance, an accidentally embedded token). Never expose query values.
    public_settings["custom_endpoint"] = _scorers.safe_endpoint_display(
        settings.get("custom_endpoint", ""))
    if settings["scorer_provider"] == _scorers.CUSTOM:
        for key in ("scorer_endpoint", "scorer_endpoint_effective", "endpoint_effective"):
            public_settings[key] = _scorers.safe_endpoint_display(settings.get(key, ""))
    decision_state = _middleware.session_state()
    sessions = [_public_entry(sid, entry)
                for sid, entry in decision_state.items()]
    # A pinned route decision and the turn memo that reuses it are two stored
    # rows, but represent one conversation and one handled request. Public
    # aggregate counters therefore count unique conversations and turn rows.
    turn_entries = [entry for entry in decision_state.values()
                    if entry.get("scope", "turn") == "turn"]
    conversation_ids = {
        str(entry.get("conversation_id") or session_id)
        for session_id, entry in decision_state.items()
    }
    # Once/fallback decisions are represented in both the turn memo and the
    # persistent route ledger. Count their scorer call once, from the ledger
    # that owns the decision, without exposing that implementation detail.
    probes = sum(
        int(_number(entry.get("probes")))
        for entry in decision_state.values()
        if (entry.get("scope", "turn") == "session_route"
            or entry.get("probe_owner", "turn") == "turn")
    )
    counts = {
        "sessions": len(conversation_ids),
        "in_flight": len(_middleware.in_flight()),
        "requests": sum(int(_number(e.get("requests"))) for e in turn_entries),
        "probes": probes,
        "decided": sum(1 for e in turn_entries if e.get("state") == "decided"),
        "failed": sum(1 for e in turn_entries if e.get("state") == "failed"),
        "unsupported": sum(1 for e in turn_entries if e.get("state") == "unsupported"),
    }
    last = _last_session(sessions)
    return {
        "schema": STATUS_SCHEMA,
        "plugin": PLUGIN_ID,
        "mode": settings["mode"],
        # "config" (the file decides) vs "override" (/hae in this process);
        # a runtime override applies to future requests only and is not persisted.
        "mode_source": settings["mode_source"],
        "settings": public_settings,
        "credential": _scorers.credential_present(
            settings["scorer_provider"], settings["custom_auth"]),
        "credential_required": _scorers.credential_required(
            settings["scorer_provider"], settings["custom_auth"]),
        "cloudflare_account_ready": settings["cloudflare_account_ready"],
        "counts": counts,
        "sessions": sessions,
        "last": last,
    }


def _render_entry(entry: Dict[str, Any]) -> str:
    state = entry.get("state")
    parts = [f"session={entry['session_id']}", f"state={state}"]
    for key in ("score", "label", "target", "decision_type", "choices", "cache_behavior",
                "provider", "model", "api_mode",
                "scorer_provider", "scorer_model", "requests",
                "probes", "elapsed_ms", "failure"):
        value = entry.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return "  " + " ".join(parts)


def _endpoint_line(settings: Dict[str, Any]) -> str:
    """Render the URL requests really go to.

    ``settings['endpoint']`` is the raw setting and ``endpoint_effective`` is what
    the client POSTs to; when they differ the raw value is shown too, so a base
    URL left in the config is never mistaken for the scoring route.
    """
    configured = str(settings.get("endpoint") or "")
    effective = str(settings.get("scorer_endpoint_effective") or configured)
    if settings.get("scorer_provider") in (_scorers.OPENROUTER, _scorers.OPENAI_DECISION):
        configured = str(settings.get("scorer_endpoint") or "")
    elif settings.get("scorer_provider") == _scorers.CLOUDFLARE:
        configured = effective
    if configured.strip().rstrip("/") != effective:
        return f"{effective} (configured: {configured})"
    return effective


def _status_text() -> str:
    """Compact, conversation-scoped status for people checking the live result."""
    payload = _status_payload()
    settings = payload["settings"]
    view = _conversation_state()
    entry = view.get("status") or {}
    model = entry.get("model") or "none observed"
    scorer = settings.get("scorer_provider") or "unknown"
    scorer_model = settings.get("scorer_model_effective") or "unset"
    credential = (
        "key present" if payload["credential"] else "key missing"
    ) if payload["credential_required"] else "key not required"
    if entry.get("cache_verdict") in {"compatible", "sensitive", "not_verified"}:
        cache_verdict = entry["cache_verdict"]
    else:
        cache_verdict = _middleware._cache_verdict(
            entry.get("provider"), entry.get("model"), entry.get("api_mode"),
            details=entry,
        ) if entry else "not_verified"
    cache_text = {
        "compatible": "compatible (effort stays out of the prompt cache key)",
        "sensitive": "cache-sensitive",
        "not_verified": "not verified",
    }[cache_verdict]
    lines = [
        "Hermes Adaptive Effort — status",
        f"Mode: {entry.get('mode') or payload['mode']} ({settings.get('mode_source', 'config')})",
        f"Scorer: {scorer} · {scorer_model} · {credential}",
        f"Model: {model}",
        f"Decision: {entry.get('state', 'no request seen')}",
        f"Cache: {cache_text}",
    ]
    label, target = entry.get("label"), entry.get("target")
    if label or target:
        lines.append(f"Selected effort: {target or 'N/A'}" +
                     (f" (rubric: {label})" if label else ""))
    latest = view.get("latest")
    if latest:
        lines.append(f"Latest applied change: {latest['from']} -> {latest['to']}")
    if entry.get("failure"):
        lines.append(f"Why unchanged: {entry['failure']}")
    lines.append("Use /hae history to see recent applied changes; /hae status full for details.")
    return "\n".join(lines)


def _status_full_text() -> str:
    """Expanded diagnostics for the active conversation, never a global session dump."""
    payload = _status_payload()
    settings = payload["settings"]
    view = _conversation_state()
    entry = view.get("status") or {}
    lines = [
        "Hermes Adaptive Effort — detailed status",
        f"Mode: {entry.get('mode') or payload['mode']} "
        f"(from {settings.get('mode_source', 'config')})",
        f"Scorer: {settings['scorer_provider']} · model={settings['scorer_model_effective'] or 'unset'}",
        f"Credential: {'present' if payload['credential'] else 'missing'}"
        if payload["credential_required"] else "Credential: not required",
        f"Endpoint: {_endpoint_line(settings)}",
        f"Decision: {entry.get('state', 'no request seen')}",
    ]
    if entry:
        for field in ("provider", "model", "api_mode", "decision_type", "choices",
                      "score", "label", "target", "cache_behavior", "cache_verdict",
                      "requests", "probes", "elapsed_ms", "failure"):
            value = entry.get(field)
            if value is not None:
                lines.append(f"{field.replace('_', ' ').title()}: {value}")
    lines.extend([
        f"Timeout: {settings['timeout_s']} s · retained turns: {settings['max_turns']} · "
        f"task text limit: {settings['prompt_chars']} characters",
        f"Classifier guidance: {'configured' if settings.get('classification_instructions_configured') else 'default'} "
        f"(contents remain private; limit={_middleware._rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS} characters)",
        f"Display: TUI={'on' if settings['show_tui_status'] else 'off'} · "
        f"Desktop popup={'on' if settings['show_desktop_popup'] else 'off'}",
    ])
    if settings.get("scorer_provider") == _scorers.CLOUDFLARE:
        lines.append(f"Cloudflare account: {'ready' if payload['cloudflare_account_ready'] else 'missing or invalid'}")
    elif settings.get("scorer_provider") == _scorers.OPENROUTER:
        lines.append("OpenRouter routing: ZDR-only endpoints; data_collection=deny")
    elif settings.get("scorer_provider") == _scorers.CUSTOM:
        lines.append(f"Custom scorer: format={settings['custom_api_format']} auth={settings['custom_auth']}")
    lines.append("History: " + ("available" if view.get("available") else "unavailable"))
    return "\n".join(lines)


def _status_json() -> str:
    return json.dumps(_status_payload(), ensure_ascii=False)


# ── probe ───────────────────────────────────────────────────────────────────

def _probe(text: str) -> str:
    """One bounded classification of the operator's own text — nothing stored."""
    result = _middleware.run_probe(text)
    return json.dumps({
        "schema": PROBE_SCHEMA,
        "text_chars": len(text),
        "score": result["score"],
        "label": result["label"],
        "failure": result["failure"],
        "elapsed_ms": result["elapsed_ms"],
        "at": time.time(),
    }, ensure_ascii=False)
