"""Source contract for the Desktop half of live decision events and selector sync."""

from pathlib import Path


DESKTOP = Path(__file__).parents[1] / "desktop" / "plugin.js"


def test_desktop_subscribes_to_live_decisions_and_session_acknowledgments():
    source = DESKTOP.read_text(encoding="utf-8")

    assert "ctx.onEvent(DECISION_EVENT" in source
    assert "ctx.onEvent(SESSION_INFO_EVENT" in source
    assert "rememberDecision(event)" in source
    assert "publishSessionInfo(event)" in source
    assert "notifyDecisionOutcome(record)" in source
    assert "syncAppliedEffort(ctx, record)" in source


def test_live_status_is_scoped_to_source_and_rejects_replays_and_old_revisions():
    source = DESKTOP.read_text(encoding="utf-8")

    assert "event?.replayed === true" in source
    assert "event?.type !== DECISION_EVENT" in source
    assert "payload.schema !== 'hermes-adaptive-effort.desktop-status.v1'" in source
    assert "liveKey(source.connectionId, source.profile, sessionId)" in source
    assert "revision <= prior.revision" in source
    assert "retired.includes(streamId)" in source
    assert "liveFor(record.connectionId, record.profile, record.sessionId)" in source


def test_native_selector_rpc_is_explicitly_session_scoped_and_confirmed():
    source = DESKTOP.read_text(encoding="utf-8")
    sync = source.split("async function syncAppliedEffort", 1)[1].split(
        "function notifyDecisionOutcome", 1
    )[0]

    assert "focused.sessionId !== record.sessionId" in sync
    assert "latestFocus.model !== record.route.model" in sync
    assert "record.selectorSyncSupported" in sync
    assert "host.requestProfile(route, 'config.set'" in sync
    assert "key: 'reasoning'" in sync
    assert "scope: 'session'" in sync
    assert "session_id: record.sessionId" in sync
    assert "waitForSessionInfo(ctx" in sync
    assert "const deadline = Date.now() + 5000" in source
    assert "const remainingMs = Math.max(1, deadline - Date.now())" in source
    assert "reasoning_effort_wire === waiter.target" in source


def test_popup_visibility_does_not_gate_event_listener_or_native_sync():
    source = DESKTOP.read_text(encoding="utf-8")
    register = source.split("register(ctx) {", 1)[1].split("\n  }\n}", 1)[0]

    assert "ctx.onEvent(DECISION_EVENT" in register
    assert "showDesktopPopup" in source
    assert "if (showDesktopPopup) host.notify(input)" in source
