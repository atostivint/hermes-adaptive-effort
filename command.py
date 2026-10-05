"""/hae — explain, inspect and steer the plugin.

``status`` and ``probe`` never classify a conversation, never rewrite a request
and never store anything: they only render what the middleware already holds.
``off|recommend|auto|cache_safe|inject`` set the mode for FUTURE requests of this
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
from typing import Any, Dict, List, Optional

from . import cache_safety as _cache_safety
from . import middleware as _middleware
from . import scorers as _scorers

PLUGIN_ID = _middleware.PLUGIN_ID
MODES = _middleware.VALID_MODES
USAGE = """Usage:
  /hae status                               Show mode, settings, credential, session counts
  /hae status json                          Machine-readable status payload
  /hae off|recommend|auto|cache_safe|inject Set the mode used by future requests
  /hae probe <text>                         Classify <text> once (prints score/label, stores nothing)
  /hae help                                 Show this help

Modes:
  off         do nothing (the default)
  recommend   classify, report the level it would use, rewrite nothing
  auto        classify and apply effort; inject on verified or operator-listed exact models
  cache_safe  route per turn only on routes where an effort change keeps the
              prompt cache; elsewhere pin one level for the whole session
  inject      cache_safe routing plus exact-model effort injection (compatibility mode)

A mode set here applies to future requests served by this process. It is not
written to config.yaml (nothing here edits your files), so it does not survive a
restart; persist it as plugins.entries.hermes-adaptive-effort.settings.mode instead.
Probe asks the configured scorer for a rubric score on the text you typed, plus any optional
configured classifier guidance — it does not use or store your session's conversation, and never
writes to session state."""

STATUS_SCHEMA = "hermes-adaptive-effort.status.v1"
PROBE_SCHEMA = "hermes-adaptive-effort.probe.v1"

#: The only session fields ever rendered — an entry may hold anything (it is
#: plugin-author payload), so prompt text and provider junk are filtered out.
_ENTRY_FIELDS = (
    "conversation_id", "state", "score", "label", "target", "mode", "provider", "model", "api_mode",
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
        return USAGE
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
        return USAGE
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
    sessions = [_public_entry(sid, entry)
                for sid, entry in _middleware.session_state().items()]
    counts = {
        "sessions": len(sessions),
        "in_flight": len(_middleware.in_flight()),
        "requests": sum(int(_number(e.get("requests"))) for e in sessions),
        "probes": sum(int(_number(e.get("probes"))) for e in sessions),
        "decided": sum(1 for e in sessions if e.get("state") == "decided"),
        "failed": sum(1 for e in sessions if e.get("state") == "failed"),
        "unsupported": sum(1 for e in sessions if e.get("state") == "unsupported"),
    }
    last = _last_session(sessions)
    route = last or {}
    return {
        "schema": STATUS_SCHEMA,
        "plugin": PLUGIN_ID,
        "mode": settings["mode"],
        # "config" (the file decides) vs "override" (/hae in this process);
        # a runtime override applies to future requests only and is not persisted.
        "mode_source": settings["mode_source"],
        "settings": public_settings,
        "cache_safety": _cache_safety.explain(
            route.get("provider"), route.get("model"), route.get("api_mode")),
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
    for key in ("score", "label", "target", "provider", "model", "api_mode",
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
    if settings.get("scorer_provider") == _scorers.OPENROUTER:
        configured = str(settings.get("scorer_endpoint") or "")
    elif settings.get("scorer_provider") == _scorers.CLOUDFLARE:
        configured = effective
    if configured.strip().rstrip("/") != effective:
        return f"{effective} (configured: {configured})"
    return effective


def _status_text() -> str:
    payload = _status_payload()
    settings = payload["settings"]
    counts = payload["counts"]
    lines = [
        "hermes-adaptive-effort status",
        f"mode: {payload['mode']} (from {settings.get('mode_source', 'config')})",
        f"cache safety: {payload['cache_safety']}",
        f"credential: {'present' if payload['credential'] else 'missing'}"
        if payload["credential_required"] else "credential: not required",
        f"scorer: {settings['scorer_provider']} model={settings['scorer_model_effective'] or 'unset'}",
        f"prompt sharing: routing sends task text and optional guidance to {settings['scorer_provider']}; probe sends typed text and optional guidance",
        f"settings: timeout_s={settings['timeout_s']} "
        f"max_turns={settings['max_turns']} "
        f"max_prompt_chars={settings['prompt_chars']}",
        f"classifier guidance: {'configured' if settings.get('classification_instructions_configured') else 'default'} "
        f"(contents are private; limit={_middleware._rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS} chars)",
        f"display: TUI={'on' if settings['show_tui_status'] else 'off'} "
        f"Desktop popup={'on' if settings['show_desktop_popup'] else 'off'}",
        f"endpoint: {_endpoint_line(settings)}",
        f"counts: sessions={counts['sessions']} in_flight={counts['in_flight']} "
        f"requests={counts['requests']} probes={counts['probes']} "
        f"decided={counts['decided']} failed={counts['failed']} "
        f"unsupported={counts['unsupported']}",
    ]
    if settings.get("scorer_provider") == _scorers.CLOUDFLARE:
        lines.insert(4, f"Cloudflare account: {'ready' if payload['cloudflare_account_ready'] else 'missing or invalid'}")
    elif settings.get("scorer_provider") == _scorers.OPENROUTER:
        lines.insert(5, "OpenRouter routing: ZDR-only endpoints; data_collection=deny")
    elif settings.get("scorer_provider") == _scorers.CUSTOM:
        lines.insert(5, f"custom scorer: format={settings['custom_api_format']} "
                    f"auth={settings['custom_auth']}")
    lines.extend(_render_entry(entry) for entry in payload["sessions"])
    last = payload["last"]
    lines.append(f"last: {_render_entry(last).strip()}" if last else "last: none")
    change = _middleware.effort_change_state()["latest"]
    lines.append(
        f"last applied effort: {change['from']} -> {change['to']}" if change
        else "last applied effort: N/A")
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
