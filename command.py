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
from . import i18n_text as _i18n

PLUGIN_ID = _middleware.PLUGIN_ID
MODES = _middleware.VALID_MODES
USAGE = """Usage:
  /hae                                      Show this conversation's effort history
  /hae history [N|all] [full]               Show recent applied effort changes
  /hae status                               Show a concise status for this conversation
  /hae status full                          Show detailed status for this conversation
  /hae status json                          Machine-readable status payload
  /hae auto|once|always|off                  Set the mode used by future requests
  /hae auto|once|always|off here            Set the mode for this conversation only
  /hae default here                         Clear this conversation's mode (use the default)
  /hae probe <text>                         Classify <text> once (prints score/label, stores nothing)
  /hae cache                                Cache-loss diagnostic for the last 7 days
  /hae cache json                           Machine-readable cache diagnostic
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
CACHE_SCHEMA = "hermes-adaptive-effort.cache.v1"

#: The only session fields ever rendered — an entry may hold anything (it is
#: plugin-author payload), so prompt text and provider junk are filtered out.
_ENTRY_FIELDS = (
    "conversation_id", "state", "score", "label", "target", "previous", "decision_type", "choices",
    "cache_behavior", "continuation", "held", "escalated", "escalation_reason", "manual_effort", "mode", "provider", "model", "api_mode",
    "dynamic", "dynamic_reason", "trial_progress",
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
        return _usage()


def _usage() -> str:
    """The English banner, translated only when a catalog is installed."""
    return _i18n._t("hae.usage", USAGE)


def _dispatch(raw_args: str) -> str:
    parts = (raw_args or "").split()
    if not parts:
        return _history_text(10)
    verb, *rest = parts
    if verb in {"help", "-h", "--help"}:
        return _usage()
    if (verb in MODES or verb == "default") and rest == ["here"]:
        return _set_conversation_mode(verb)
    if verb in MODES:
        if rest:
            return _usage()
        return _set_mode(verb)
    if verb == "status":
        if not rest:
            return _status_text()
        if rest == ["json"]:
            return _status_json()
        if rest == ["full"]:
            return _status_full_text()
        return _usage()
    if verb == "history":
        parsed = _parse_history_args(rest)
        return _history_text(*parsed) if parsed is not None else _usage()
    if verb == "probe":
        if not rest:
            return _usage()
        return _probe(" ".join(rest))
    if verb == "cache":
        if not rest:
            return _cache_text()
        if rest == ["json"]:
            return _cache_json()
        return _usage()
    return _usage()


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
        return _usage()
    if applied == before:
        return _i18n._t(
            "hae.mode.unchanged",
            "hermes-adaptive-effort mode: {mode} (unchanged; applies to future requests "
            "in this process, not persisted)", mode=applied)
    return _i18n._t(
        "hae.mode.changed",
        "hermes-adaptive-effort mode: {before} -> {after} (applies to future requests in "
        "this process; not persisted, set plugins.entries.{plugin}.settings.mode to make it "
        "stick)", before=before, after=applied, plugin=PLUGIN_ID)


def _set_conversation_mode(target: str) -> str:
    """Set or clear this conversation's mode override; the default mode is untouched."""
    scope, identities = _history_store.current_identity()
    if not scope:
        return _i18n._t(
            "hae.conv.identity_missing",
            "hermes-adaptive-effort: no active conversation identity here; mode unchanged")
    before = _middleware.conversation_mode_override(identities)
    try:
        after = _middleware.set_conversation_mode(scope, target)
    except RuntimeError:
        return _i18n._t(
            "hae.conv.storage_unavailable",
            "hermes-adaptive-effort: conversation mode storage is unavailable; mode unchanged")
    _middleware.clear_manual_effort(identities)
    return _i18n._t(
        "hae.conv.set",
        "hermes-adaptive-effort conversation mode: {before} -> {after} (this conversation only; "
        "default mode stays {default_mode})",
        before=before or "default", after=after or "default",
        default_mode=_middleware._settings()["mode"])


def _conversation_mode_view(conversation_id: Optional[str], default_mode: str) -> Dict[str, Any]:
    """Describe the override for an explicit id, else for the command's own conversation."""
    scope: Optional[str]
    if conversation_id:
        scope, identities = str(conversation_id), [str(conversation_id)]
    else:
        scope, identities = _history_store.current_identity()
    override = _middleware.conversation_mode_override(identities) if scope else None
    return {
        "conversation_id": scope or None,
        "mode": override,
        "effective_mode": override or default_mode,
        "source": "conversation" if override else "default",
    }


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
    # The stand-down lives in memory only (never in the history store); overlay it here.
    if view.get("status") and any(_middleware.manual_effort_standing_down(i) for i in identities):
        view["status"] = dict(view["status"], manual_effort=True)
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
        "compatible": _i18n._t("hae.cache.verdict_compatible", "Compatible"),
        "sensitive": _i18n._t("hae.cache.verdict_sensitive", "Sensitive"),
        "not_verified": _i18n._t("hae.cache.verdict_not_verified", "Not verified"),
    }.get(str(verdict or ""), _i18n._t("hae.cache.verdict_not_verified", "Not verified"))


def _local_time(at: Any, *, full: bool = False) -> str:
    try:
        value = datetime.fromtimestamp(float(at)).astimezone()
        return value.strftime("%Y-%m-%d %H:%M:%S %z" if full else "%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return _i18n._t("hae.common.unknown", "unknown")


def _model_name(value: Any) -> str:
    model = str(value or "")
    if not model:
        return _i18n._t("hae.common.unknown", "unknown")
    return model if len(model) <= 28 else model[:25] + "..."


def _render_history_event(event: Dict[str, Any], *, full: bool = False) -> List[str]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    unknown = _i18n._t("hae.common.unknown", "unknown")
    model = str(event.get("model") or details.get("model") or unknown)
    transition = f"{event.get('from', '?')} -> {event.get('to', '?')}"
    cache = _cache_label(event.get("cache_verdict"))
    if not full:
        return [f"{_local_time(event.get('at')):<17} {_model_name(model):<28} "
                f"{transition:<15} {cache}"]
    lines = [f"{_local_time(event.get('at'), full=True)}  {model}",
             "  " + _i18n._t("hae.history.effort", "effort: {transition}",
                             transition=transition),
             "  " + _i18n._t("hae.history.cache", "cache: {cache}", cache=cache),
             "  " + _i18n._t("hae.history.provider", "provider: {provider}",
                             provider=event.get('provider') or details.get('provider') or unknown),
             "  " + _i18n._t("hae.history.api_route", "API route: {route}",
                             route=event.get('api_mode') or details.get('api_mode') or unknown)]
    for field, label in (("score", "score"),
                         ("label", _i18n._t("hae.history.detail_rubric", "rubric label")),
                         ("target", _i18n._t("hae.history.detail_target", "route target")),
                         ("scorer_provider", _i18n._t("hae.history.detail_scorer", "scorer")),
                         ("scorer_model", _i18n._t("hae.history.detail_scorer_model", "scorer model")),
                         ("elapsed_ms", _i18n._t("hae.history.detail_time", "scoring time")),
                         ("failure", _i18n._t("hae.history.detail_result", "result"))):
        value = details.get(field)
        if value is not None:
            suffix = " ms" if field == "elapsed_ms" else ""
            lines.append(f"  {label}: {value}{suffix}")
    return lines


def _history_text(limit: int = 10, full: bool = False) -> str:
    view = _conversation_state(limit)
    lines = [_i18n._t("hae.history.title", "Hermes Adaptive Effort — recent changes")]
    if view.get("identity_missing"):
        return "\n".join(lines + [_i18n._t(
            "hae.history.identity_missing",
            "No active conversation identity is available in this command context.")])
    if not view.get("available"):
        return "\n".join(lines + [_i18n._t(
            "hae.history.storage_unavailable",
            "History storage is unavailable. Request routing is unaffected.")])
    events = view.get("events") or []
    mode = (view.get("status") or {}).get("mode") or _middleware._settings()["mode"]
    lines.append(_i18n._t("hae.history.summary",
                          "Current conversation · mode: {mode} · {count} shown",
                          mode=mode, count=len(events)))
    lines.append(_i18n._t("hae.history.timezone", "Time zone: {zone}",
                          zone=datetime.now().astimezone().strftime("%z")))
    if not events:
        lines.append(_i18n._t("hae.history.empty", "No applied effort changes recorded yet."))
    elif full:
        for index, event in enumerate(events):
            if index:
                lines.append("")
            lines.extend(_render_history_event(event, full=True))
    else:
        lines.append("")
        lines.append(f"{_i18n._t('hae.history.col_time', 'Time'):<17} "
                     f"{_i18n._t('hae.history.col_model', 'Model'):<28} "
                     f"{_i18n._t('hae.history.col_effort', 'Effort'):<15} "
                     f"{_i18n._t('hae.history.col_cache', 'Cache')}")
        lines.extend(_render_history_event(event)[0] for event in events)
    lines.append("")
    lines.append("/hae status · /hae history all · /hae history full · /hae help")
    return "\n".join(lines)


def _status_payload(conversation_id: Optional[str] = None) -> Dict[str, Any]:
    settings = _middleware._settings()
    public_settings = dict(settings)
    # Operator guidance is sent only to the selected scorer for a classification;
    # never return its contents through status, Desktop polling, or CLI JSON.
    configured_guidance = public_settings.pop("classification_instructions", "")
    public_settings["classification_instructions_configured"] = bool(configured_guidance)
    public_settings["classification_instructions_chars"] = len(configured_guidance)
    public_settings["continuation"] = _middleware.continuation_state(settings)
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
        # "mode" stays the default (global) mode; the conversation block carries the override.
        "default_mode": settings["mode"],
        "conversation": _conversation_mode_view(conversation_id, settings["mode"]),
        "settings": public_settings,
        "credential": _scorers.credential_present(
            settings["scorer_provider"], settings["custom_auth"]),
        "credential_required": _scorers.credential_required(
            settings["scorer_provider"], settings["custom_auth"]),
        "cloudflare_account_ready": settings["cloudflare_account_ready"],
        "scorer_pause": _middleware.scorer_pause_state(settings),
        "scorer_latency": _middleware.scorer_latency_state(settings),
        "label_distribution": _middleware.label_distribution_state(settings),
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
    if settings.get("scorer_provider") in (_scorers.OPENROUTER, _scorers.OPENAI_DECISION,
                                         _scorers.NACE, _scorers.OLLAMA):
        configured = str(settings.get("scorer_endpoint") or "")
    elif settings.get("scorer_provider") == _scorers.CLOUDFLARE:
        configured = effective
    if configured.strip().rstrip("/") != effective:
        return effective + _i18n._t("hae.full.endpoint_configured",
                                    " (configured: {configured})", configured=configured)
    return effective


def _conversation_mode_line(payload: Dict[str, Any]) -> str:
    view = payload.get("conversation") or {}
    default = payload.get("default_mode") or payload.get("mode")
    if view.get("mode"):
        return _i18n._t(
            "hae.conv.line_override",
            "conversation mode: {mode} (this conversation) · default: {mode_default}",
            mode=view["mode"], mode_default=default)
    return _i18n._t("hae.conv.line_default", "conversation mode: default ({mode_default})",
                    mode_default=default)


#: ``dynamic_reason`` codes -> the human phrase the status line shows. The codes
#: themselves are machine values and are never translated; only these phrases are.
_DYNAMIC_REASON_KEYS = {
    "listed": ("hae.reason.listed", "listed"),
    "operator": ("hae.reason.operator", "operator-listed"),
    "observed": ("hae.reason.observed", "observed"),
    "observed_cache_loss": ("hae.reason.observed_cache_loss", "observed cache loss"),
    "rejected_level": ("hae.reason.rejected_level", "provider rejected a trial level"),
    "no_cache_data": ("hae.reason.no_cache_data", "no cache data from the provider"),
    "api_mode": ("hae.reason.api_mode", "cache-hostile API mode"),
    "trial_disabled": ("hae.reason.trial_disabled", "trial off"),
}
_TRIAL_TARGET = _middleware._route_trial.SUCCESSES_REQUIRED


def _dynamic_reason_text(reason: Any) -> Optional[str]:
    """Human phrase for a ``dynamic_reason`` code, or ``None`` for an unknown one."""
    entry = _DYNAMIC_REASON_KEYS.get(reason)
    return _i18n._t(*entry) if entry else None


def _effort_changes_line(entry: Dict[str, Any]) -> Optional[str]:
    """One line saying whether the route changes effort every turn, and why."""
    if not entry or not entry.get("state"):
        return None
    reason = entry.get("dynamic_reason")
    if entry.get("dynamic") is True and reason == "trial":
        done = int(_number(entry.get("trial_progress")))
        text = _i18n._t("hae.changes.trial", "Effort changes: every turn — on trial "
                        "({done}/{target})", done=done, target=_TRIAL_TARGET)
    elif entry.get("dynamic") is True:
        why = (_dynamic_reason_text(reason) or reason
               or _i18n._t("hae.reason.listed", "listed"))
        text = _i18n._t("hae.changes.dynamic", "Effort changes: every turn — dynamic ({reason})",
                        reason=why)
    else:
        why = _dynamic_reason_text(reason)
        if why:
            text = _i18n._t("hae.changes.pinned_why",
                            "Effort changes: once, then kept — not dynamic ({reason})", reason=why)
        else:
            text = _i18n._t("hae.changes.pinned",
                            "Effort changes: once, then kept — not dynamic")
    mode = entry.get("mode")
    if mode == "once":
        text += _i18n._t("hae.changes.mode_once",
                         "; mode once always keeps one decision")
    elif mode == "always" and entry.get("dynamic") is not True:
        text += _i18n._t("hae.changes.mode_always",
                         "; mode always changes every turn anyway (prompt-cache risk)")
    elif mode == "off":
        text += _i18n._t("hae.changes.mode_off", "; mode off changes nothing")
    return text


def _status_text() -> str:
    """Compact, conversation-scoped status for people checking the live result."""
    payload = _status_payload()
    settings = payload["settings"]
    view = _conversation_state()
    entry = view.get("status") or {}
    unknown = _i18n._t("hae.common.unknown", "unknown")
    model = entry.get("model") or _i18n._t("hae.status.no_model", "none observed")
    scorer = settings.get("scorer_provider") or unknown
    scorer_model = settings.get("scorer_model_effective") or _i18n._t("hae.common.unset", "unset")
    credential = (
        _i18n._t("hae.status.cred_present", "key present") if payload["credential"]
        else _i18n._t("hae.status.cred_missing", "key missing")
    ) if payload["credential_required"] else _i18n._t(
        "hae.status.cred_not_required", "key not required")
    if entry.get("cache_verdict") in {"compatible", "sensitive", "not_verified"}:
        cache_verdict = entry["cache_verdict"]
    else:
        cache_verdict = _middleware._cache_verdict(
            entry.get("provider"), entry.get("model"), entry.get("api_mode"),
            details=entry,
        ) if entry else "not_verified"
    cache_text = {
        "compatible": _i18n._t("hae.status.cache_compatible",
                               "compatible (effort stays out of the prompt cache key)"),
        "sensitive": _i18n._t("hae.status.cache_sensitive", "cache-sensitive"),
        "not_verified": _i18n._t("hae.status.cache_not_verified", "not verified"),
    }[cache_verdict]
    lines = [
        _i18n._t("hae.status.title", "Hermes Adaptive Effort — status"),
        _i18n._t("hae.status.mode", "Mode: {mode} ({source})",
                 mode=entry.get('mode') or payload['mode'],
                 source=settings.get('mode_source', 'config')),
        _conversation_mode_line(payload),
        _i18n._t("hae.status.scorer", "Scorer: {provider} · {model} · {credential}",
                 provider=scorer, model=scorer_model, credential=credential),
        _i18n._t("hae.status.model", "Model: {model}", model=model),
        _i18n._t("hae.status.decision", "Decision: {state}",
                 state=entry.get('state', _i18n._t("hae.status.no_request",
                                                   "no request seen"))),
        _i18n._t("hae.status.cache", "Cache: {cache}", cache=cache_text),
    ]
    changes_line = _effort_changes_line(entry)
    if changes_line:
        lines.append(changes_line)
    if entry.get("manual_effort"):
        lines.append(_i18n._t(
            "hae.status.manual_effort",
            "Manual effort: you changed the effort by hand, so the plugin is standing "
            "down for this conversation (no scorer call, no rewrite). "
            "Re-enable with /hae <mode> here"))
    label, target = entry.get("label"), entry.get("target")
    if label or target:
        lines.append(_i18n._t("hae.status.selected", "Selected effort: {target}",
                              target=target or 'N/A') +
                     (_i18n._t("hae.status.selected_rubric", " (rubric: {label})", label=label)
                      if label else ""))
    latest = view.get("latest")
    if latest:
        lines.append(_i18n._t("hae.status.latest_change",
                              "Latest applied change: {before} -> {after}",
                              before=latest['from'], after=latest['to']))
    if entry.get("failure"):
        lines.append(_i18n._t("hae.status.why_unchanged", "Why unchanged: {failure}",
                              failure=entry['failure']))
    pause = payload.get("scorer_pause") or {}
    if pause.get("paused"):
        lines.append(_i18n._t("hae.status.paused",
                              "Scorer paused: {remaining} s left ({failures} failures)",
                              remaining=pause.get('remaining_s', 0),
                              failures=pause.get('failures', 0)))
    latency_line = _latency_line(payload, always=False)
    if latency_line:
        lines.append(latency_line)
    lines.extend(_distribution_lines(payload, entry, limit=1))
    lines.append(_i18n._t(
        "hae.status.footer",
        "Use /hae history to see recent applied changes; /hae status full for details."))
    return "\n".join(lines)


def _latency_line(payload: Dict[str, Any], *, always: bool) -> Optional[str]:
    """One p95 line for the selected scorer; ``None`` without samples unless *always*."""
    latency = payload.get("scorer_latency") or {}
    count = int(latency.get("count") or 0)
    if not count:
        return (_i18n._t("hae.latency.none", "Scorer latency: no calls yet")
                if always else None)
    if latency.get("p95_over_max"):
        bound = f"> {_middleware._LATENCY_BUCKETS_MS[-1]} ms"
    else:
        bound = f"\u2264 {latency.get('p95_le_ms')} ms"
    return _i18n._t("hae.latency.p95", "Scorer latency: p95 {bound} ({count} calls)",
                    bound=bound, count=count)


def _distribution_lines(payload: Dict[str, Any], entry: Dict[str, Any],
                        *, limit: int) -> List[str]:
    """Compact scorer-label count lines, the entry's own route first; informational only."""
    routes = list((payload.get("label_distribution") or {}).get("routes") or [])
    own = (entry.get("provider"), entry.get("model"), entry.get("api_mode"))
    def route_key(route):
        return (route.get("provider"), route.get("model"), route.get("api_mode"))

    routes = ([r for r in routes if route_key(r) == own]
              + [r for r in reversed(routes) if route_key(r) != own])
    levels = _middleware._effort.EFFORT_ORDER
    def level_order(name):
        return (levels.index(name) if name in levels else len(levels), name)

    lines = []
    for route in routes[:limit]:
        counts = route.get("counts") or {}
        text = " \u00b7 ".join(f"{name} {counts[name]}" for name in sorted(counts, key=level_order))
        line = _i18n._t("hae.labels.line", "Effort labels ({model}): {text}",
                        model=route.get('model') or _i18n._t("hae.common.unknown", "unknown"),
                        text=text)
        dominant = route.get("dominant")
        if dominant:
            line += _i18n._t("hae.labels.dominates", " \u00b7 {label} dominates "
                              "({pct}% of {total})", label=dominant['label'],
                              pct=round(dominant['share'] * 100), total=route.get('total'))
        lines.append(line)
    return lines


def _status_full_text() -> str:
    """Expanded diagnostics for the active conversation, never a global session dump."""
    payload = _status_payload()
    settings = payload["settings"]
    view = _conversation_state()
    entry = view.get("status") or {}
    on, off = _i18n._t("hae.common.on", "on"), _i18n._t("hae.common.off", "off")
    lines = [
        _i18n._t("hae.full.title", "Hermes Adaptive Effort — detailed status"),
        _i18n._t("hae.full.mode", "Mode: {mode} (from {source})",
                 mode=entry.get('mode') or payload['mode'],
                 source=settings.get('mode_source', 'config')),
        _conversation_mode_line(payload),
        _i18n._t("hae.full.scorer", "Scorer: {provider} · model={model}",
                 provider=settings['scorer_provider'],
                 model=settings['scorer_model_effective'] or _i18n._t("hae.common.unset",
                                                                      "unset")),
        _i18n._t("hae.full.credential_present", "Credential: present")
        if payload['credential'] else _i18n._t("hae.full.credential_missing",
                                              "Credential: missing")
        if payload["credential_required"] else _i18n._t(
            "hae.full.credential_not_required", "Credential: not required"),
        _i18n._t("hae.full.endpoint", "Endpoint: {endpoint}", endpoint=_endpoint_line(settings)),
        _i18n._t("hae.full.decision", "Decision: {state}",
                 state=entry.get('state', _i18n._t("hae.status.no_request",
                                                   "no request seen"))),
    ]
    if entry:
        for field in ("provider", "model", "api_mode", "decision_type", "choices",
                      "score", "label", "target", "continuation", "held", "escalated", "escalation_reason",
                      "dynamic", "dynamic_reason", "trial_progress", "manual_effort", "cache_behavior", "cache_verdict",
                      "requests", "probes", "elapsed_ms", "failure"):
            value = entry.get(field)
            if value is not None:
                lines.append(f"{field.replace('_', ' ').title()}: {value}")
    lines.extend([
        _i18n._t("hae.full.timeout",
                 "Timeout: {timeout} s ({source}) · retained turns: {turns} · "
                 "task text limit: {chars} characters",
                 timeout=settings['timeout_s'],
                 source=(_i18n._t("hae.common.configured", "configured")
                         if settings.get('timeout_source') == 'configured'
                         else _i18n._t("hae.common.provider_default", "provider default")),
                 turns=settings['max_turns'], chars=settings['prompt_chars']),
        _i18n._t("hae.full.guidance",
                 "Classifier guidance: {state} (contents remain private; limit={limit} "
                 "characters)",
                 state=(_i18n._t("hae.common.configured", "configured")
                        if settings.get('classification_instructions_configured')
                        else _i18n._t("hae.common.default", "default")),
                 limit=_middleware._rubric.MAX_CLASSIFICATION_INSTRUCTION_CHARS),
        *([_i18n._t("hae.full.dialogue",
                    "Dialogue context: up to {count} earlier user/assistant messages, "
                    "{chars} characters each (contents remain private)",
                    count=settings['dialogue_context_messages'],
                    chars=_middleware.DIALOGUE_MESSAGE_CHARS)]
          if settings.get("dialogue_context_messages") else []),
        *([_i18n._t("hae.full.continuation", "Continuation: {value}",
                    value=settings['continuation'])]
          if settings.get("continuation_downgrade_votes") else []),
        _i18n._t("hae.full.bounds", "Effort bounds: floor={floor} · ceiling={ceiling}",
                 floor=settings.get('effort_floor') or off,
                 ceiling=settings.get('effort_ceiling') or off),
        _i18n._t("hae.full.tool_error", "Tool error escalation: {state}",
                 state=on if settings.get('tool_error_escalation') else off),
        _i18n._t("hae.full.goal", "Goal continuation escalation: {state}",
                 state=on if settings.get('goal_continuation_escalation') else off),
        _i18n._t("hae.full.manual", "Respect manual effort: {state}",
                 state=on if settings.get('respect_manual_effort') else off),
        _i18n._t("hae.full.trial", "Dynamic trial: {state}",
                 state=on if settings.get('dynamic_trial') else off),
        _i18n._t("hae.full.display",
                 "Display: TUI={tui} · Desktop popup={popup} · debug={debug}",
                 tui=on if settings['show_tui_status'] else off,
                 popup=on if settings['show_desktop_popup'] else off,
                 debug=on if settings.get('debug') else off),
    ])
    if settings.get("scorer_provider") == _scorers.CLOUDFLARE:
        lines.append(_i18n._t("hae.full.cloudflare", "Cloudflare account: {state}",
                              state=(_i18n._t("hae.full.cloudflare_ready", "ready")
                                     if payload['cloudflare_account_ready']
                                     else _i18n._t("hae.full.cloudflare_missing",
                                                   "missing or invalid"))))
    elif settings.get("scorer_provider") == _scorers.OPENROUTER:
        lines.append(_i18n._t("hae.full.openrouter",
                              "OpenRouter routing: ZDR-only endpoints; data_collection=deny"))
    elif settings.get("scorer_provider") == _scorers.CUSTOM:
        lines.append(_i18n._t("hae.full.custom", "Custom scorer: format={format} auth={auth}",
                              format=settings['custom_api_format'],
                              auth=settings['custom_auth']))
    lines.append(_latency_line(payload, always=True))
    lines.extend(_distribution_lines(payload, entry, limit=5)
                 or [_i18n._t("hae.labels.none_yet", "Effort labels: none yet")])
    lines.append(_i18n._t("hae.full.history", "History: {state}",
                          state=(_i18n._t("hae.common.available", "available")
                                 if view.get("available")
                                 else _i18n._t("hae.common.unavailable", "unavailable"))))
    return "\n".join(lines)


def _status_json() -> str:
    return json.dumps(_status_payload(), ensure_ascii=False)


# ── cache diagnostic ────────────────────────────────────────────────────────

#: ``excluded_*`` counters -> the human phrase the cache line and footnote show.
#: The counter names themselves are machine values and are never translated.
_CACHE_EXCLUSION_KEYS = {
    "excluded_session": ("hae.cache.excl_session", "new session"),
    "excluded_idle": ("hae.cache.excl_idle", "idle gap"),
    "excluded_shrank": ("hae.cache.excl_shrank", "compaction"),
    "excluded_no_counter": ("hae.cache.excl_no_counter", "no counter"),
    "excluded_already_cold": ("hae.cache.excl_already_cold", "already cold"),
}


def _exclusion_label(name: Any) -> str:
    """Human phrase for an ``excluded_*`` counter, else the raw code, else ``none``."""
    entry = _CACHE_EXCLUSION_KEYS.get(name)
    if entry:
        return _i18n._t(*entry)
    return str(name or "") or _i18n._t("hae.cache.excl_none", "none")


def _cache_rows() -> List[Dict[str, Any]]:
    """Flush pending counters first so the verb shows what actually happened."""
    _middleware._cache_stats.flush()
    return _middleware._cache_stats.read_rows(7)


def _cache_json() -> str:
    rows = []
    for row in _cache_rows():
        provider, model, api_mode = row["route"]
        prompt, cached = int(row.get("prompt_tokens") or 0), int(row.get("cached_tokens") or 0)
        excluded = sum(int(row.get(name) or 0) for name in _middleware._cache_stats.EXCLUSIONS)
        rows.append({
            "day": row["day"],
            "provider": provider,
            "model": model,
            "api_mode": api_mode,
            "requests": int(row.get("requests") or 0),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "cache_ratio": round(cached / prompt, 4) if prompt else None,
            "changes": int(row.get("changes") or 0),
            "cold_after_change": int(row.get("cold_after_change") or 0),
            "kept_after_change": int(row.get("kept_after_change") or 0),
            "between": int(row.get("between") or 0),
            "excluded": excluded,
            "excluded_dominant": _middleware._cache_stats.dominant_exclusion(row),
            "excluded_detail": {name: int(row.get(name) or 0)
                                for name in _middleware._cache_stats.EXCLUSIONS},
        })
    return json.dumps({"schema": CACHE_SCHEMA, "days": 7, "rows": rows}, ensure_ascii=False)


def _cache_text() -> str:
    rows = _cache_rows()
    title = _i18n._t("hae.cache.title", "Hermes Adaptive Effort — cache (last 7 days)")
    if not rows:
        return title + "\n" + _i18n._t(
            "hae.cache.empty",
            "No cache counters recorded yet. This diagnostic fills as requests complete;\n"
            "it counts only the loss attributable to this plugin's own effort change.")
    lines = [title]
    for row in rows:
        provider, model, api_mode = row["route"]
        prompt, cached = int(row.get("prompt_tokens") or 0), int(row.get("cached_tokens") or 0)
        ratio = f"{100.0 * cached / prompt:.1f}%" if prompt else "n/a"
        excluded = sum(int(row.get(name) or 0) for name in _middleware._cache_stats.EXCLUSIONS)
        dominant = _middleware._cache_stats.dominant_exclusion(row)
        lines.append(_i18n._t(
            "hae.cache.row",
            "{day}  {route}  req {requests}  cache {ratio}  changes {changes}  "
            "cold {cold}  kept {kept}  between {between}  excluded {excluded} ({why})",
            day=row['day'], route=f"{provider}/{model} ({api_mode})",
            requests=int(row.get('requests') or 0), ratio=ratio,
            changes=int(row.get('changes') or 0),
            cold=int(row.get('cold_after_change') or 0),
            kept=int(row.get('kept_after_change') or 0),
            between=int(row.get('between') or 0), excluded=excluded,
            why=_exclusion_label(dominant)))
    lines.append(_i18n._t(
        "hae.cache.footnote",
        "cold = a change that cost the prefix cache; excluded = not attributable "
        "(idle gap, new session, compaction, no counter, already cold)."))
    return "\n".join(lines)


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
