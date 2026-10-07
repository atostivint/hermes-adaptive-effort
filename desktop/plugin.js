/** Hermes Adaptive Effort — desktop toggle + live status for Hermes Desktop.
 *
 * Unified-package half of the agent plugin: install location is
 * `~/.hermes/plugins/hermes-adaptive-effort/desktop/plugin.js` (copied app-level by the
 * main process). Plain ESM, no build step: only `@hermes/plugin-sdk`, `react`
 * and `react/jsx-runtime` resolve.
 *
 * Backend is the sibling `dashboard/plugin_api.py` (`/api/plugins/hermes-adaptive-effort/`),
 * itself a thin wrapper around the agent half's command/middleware. Everything
 * fails open: backend disabled or unreachable renders `Effort: N/A`, actions toast.
 */
import {
  Codicon,
  PALETTE_AREA,
  Popover,
  PopoverContent,
  PopoverTrigger,
  STATUSBAR_AREAS,
  Tip,
  host,
  haptic,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const ID = 'hermes-adaptive-effort'
const MODES = ['auto', 'once', 'always', 'off']
const MODE_HELP = {
  auto: 'Recheck each turn when supported; otherwise reuse per route.',
  once: 'Keep one decision per route in this conversation.',
  always: 'Score every new user message.',
  off: 'No scoring or request changes.'
}
const OUTCOME_HELP = {
  reasoning_disabled: 'Reasoning is disabled for this request.',
  effort_control_unsupported: 'This model route has no supported effort setting.',
  effort_value_unsupported: 'This route has no compatible value for the selected effort.',
  invalid_prompt: 'There was no user text available to score.',
  credential_missing: 'The scorer credential is missing.',
  model_missing: 'The scorer model is not configured.',
  account_missing: 'The scorer account ID is missing.',
  account_invalid: 'The scorer account ID is invalid.',
  endpoint_missing: 'The scorer endpoint is not configured.',
  endpoint_invalid: 'The scorer endpoint is invalid.',
  unsupported_provider: 'The selected scorer provider is not supported.',
  unsupported_api_format: 'The selected scorer API format is not supported.',
  unsupported_auth: 'The selected scorer authentication method is not supported.',
  http_error: 'The scorer endpoint returned an HTTP error.',
  timeout: 'The scorer request timed out.',
  transport_error: 'The scorer could not be reached.',
  malformed_response: 'The scorer returned an invalid score.',
  classifier_error: 'The scorer could not classify this request.',
  unexpected_error: 'The scorer could not classify this request.'
}
const DECISION_EVENT = `plugin.${ID}.decision.updated`
const SESSION_INFO_EVENT = 'session.info'
const EFFORT_VALUES = new Set(['minimal', 'low', 'medium', 'high', 'xhigh', 'max'])
const MAX_LIVE_SESSIONS = 64
const MAX_RETIRED_STREAMS = 64
const MAX_SYNC_ATTEMPTS = 128
const MAX_APPLIED_NOTICE_ATTEMPTS = 128
let rest = null
let showDesktopPopup = false
const liveDecisions = new Map()
const sessionIdentities = new Map()
const activeStreams = new Map()
const retiredStreams = new Map()
const syncAttempts = new Set()
const appliedNoticeAttempts = new Set()
const liveSubscribers = new Set()
const syncAcks = new Map()
let liveRevision = 0

const ownerKey = (connectionId, profile) => JSON.stringify([
  String(connectionId || '').trim(), String(profile || '').trim() || 'default'
])
const liveKey = (connectionId, profile, sessionId) => JSON.stringify([
  String(connectionId || '').trim(), String(profile || '').trim() || 'default', String(sessionId || '')
])

function emitLiveChange() {
  liveRevision += 1
  for (const listener of liveSubscribers) listener(liveRevision)
}

function effortNotice(input) {
  if (showDesktopPopup) host.notify(input)
}

function subscribeLive(listener) {
  liveSubscribers.add(listener)
  return () => liveSubscribers.delete(listener)
}

function liveFor(connectionId, profile, sessionId) {
  return liveDecisions.get(liveKey(connectionId, profile, sessionId)) || null
}

function publicStatus(entry) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null
  const allowed = [
    'conversation_id', 'state', 'score', 'label', 'target', 'decision_type', 'choices',
    'cache_behavior', 'mode', 'provider', 'model',
    'api_mode', 'scorer_provider', 'scorer_model', 'requests', 'probes', 'elapsed_ms',
    'failure', 'updated_at'
  ]
  const result = {}
  for (const field of allowed) {
    const value = entry[field]
    if (typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value)) ||
        (field === 'choices' && Array.isArray(value) &&
          value.every(item => typeof item === 'string' && EFFORT_VALUES.has(item)))) {
      result[field] = value
    }
  }
  return result
}

function sameRoute(left, right) {
  return Boolean(left && right &&
    left.provider === right.provider && left.model === right.model && left.api_mode === right.api_mode)
}

function latestStillMatchesApplication(record, latest, appliedId, target) {
  if (!latest || latest.streamId !== record.streamId || latest.clear ||
      latest.status?.state !== 'decided' || latest.status?.mode === 'off' ||
      latest.status?.target !== target || !sameRoute(latest.route, record.route)) return false
  return !latest.applied || latest.applied.id === appliedId
}

function currentFocusedIdentity() {
  const state = host.state
  const sessionId = state.focusedSessionId?.get?.()
  const owner = state.focusedSessionOwner?.get?.()
  const storedSessionId = state.focusedStoredSessionId?.get?.()
  const mapped = sessionIdentities.get(liveKey(owner?.connectionId, owner?.profile, sessionId))
  return {
    sessionId: typeof sessionId === 'string' ? sessionId : '',
    conversationId: mapped || (typeof storedSessionId === 'string' ? storedSessionId : ''),
    connectionId: typeof owner?.connectionId === 'string' ? owner.connectionId.trim() : '',
    profile: typeof owner?.profile === 'string' && owner.profile.trim() ? owner.profile.trim() : 'default',
    activeConnectionId: typeof state.connectionId?.get?.() === 'string' ? state.connectionId.get().trim() : '',
    activeProfile: typeof state.profile?.get?.() === 'string' && state.profile.get().trim()
      ? state.profile.get().trim()
      : 'default',
    model: typeof state.model?.get?.() === 'string' ? state.model.get().trim() : ''
  }
}

function eventOwner(event) {
  const connectionId = typeof event?.connectionId === 'string' ? event.connectionId.trim() : ''
  const profile = typeof event?.profile === 'string' && event.profile.trim() ? event.profile.trim() : ''
  return connectionId && profile ? { connectionId, profile } : null
}

function inferLocalPrimaryOwner(event) {
  // GatewayEvent omits connectionId on the local/legacy primary path. Infer
  // that sentinel only while both focused and active SDK owners prove local.
  if (typeof event?.connectionId === 'string' && event.connectionId.trim()) return null
  const profile = typeof event?.profile === 'string' && event.profile.trim() ? event.profile.trim() : ''
  if (!profile) return null
  const focused = currentFocusedIdentity()
  if (focused.connectionId !== 'local' || focused.activeConnectionId !== 'local' ||
      focused.profile !== profile) return null
  return { connectionId: 'local', profile, localPrimaryInferred: true }
}

function eventConversationId(payload) {
  // Middleware sees the agent's stored conversation id, not the gateway's
  // temporary runtime id. Older payloads mislabeled it runtime_session_id.
  const value = payload?.conversation_id ?? payload?.runtime_session_id
  return typeof value === 'string' ? value.trim() : ''
}

function rememberSessionIdentity(event) {
  if (event?.replayed === true || event?.type !== SESSION_INFO_EVENT) return
  const source = eventOwner(event) || inferLocalPrimaryOwner(event)
  const runtimeId = typeof event.session_id === 'string' ? event.session_id.trim() : ''
  const storedId = typeof event.payload?.stored_session_id === 'string'
    ? event.payload.stored_session_id.trim() : ''
  if (!source || !runtimeId || !storedId) return
  const key = liveKey(source.connectionId, source.profile, runtimeId)
  if (sessionIdentities.get(key) === storedId) return
  sessionIdentities.delete(key)
  sessionIdentities.set(key, storedId)
  while (sessionIdentities.size > MAX_LIVE_SESSIONS) {
    sessionIdentities.delete(sessionIdentities.keys().next().value)
  }
  emitLiveChange()
}

function rememberDecision(event) {
  if (event?.replayed === true || event?.type !== DECISION_EVENT) return null
  const source = eventOwner(event) || inferLocalPrimaryOwner(event)
  const payload = event.payload
  if (!source || !payload || ![
    'hermes-adaptive-effort.desktop-status.v1',
    'hermes-adaptive-effort.desktop-status.v2'
  ].includes(payload.schema)) return null
  const sessionId = eventConversationId(payload)
  const streamId = typeof payload.stream_id === 'string' ? payload.stream_id : ''
  const revision = Number(payload.revision)
  if (!sessionId || !streamId || !Number.isSafeInteger(revision) || revision < 1) return null
  const routeKeys = ['provider', 'model', 'api_mode']
  if (!payload.route || routeKeys.some(key => typeof payload.route[key] !== 'string' ||
      (payload.clear !== true && !payload.route[key].trim()))) return null
  if (payload.status?.conversation_id && payload.status.conversation_id !== sessionId) return null

  const key = liveKey(source.connectionId, source.profile, sessionId)
  const prior = liveDecisions.get(key)
  const retired = retiredStreams.get(key) || []
  if (retired.includes(streamId)) return null
  if (prior?.streamId === streamId && revision <= prior.revision) return null
  if (prior && prior.streamId !== streamId) {
    const nextRetired = [prior.streamId, ...retired.filter(value => value !== prior.streamId)]
      .slice(0, MAX_RETIRED_STREAMS)
    retiredStreams.set(key, nextRetired)
  }
  activeStreams.set(key, streamId)

  const record = {
    connectionId: source.connectionId,
    profile: source.profile,
    localPrimaryInferred: source.localPrimaryInferred === true,
    sessionId,
    streamId,
    revision,
    route: {
      provider: payload.route.provider,
      model: payload.route.model,
      api_mode: payload.route.api_mode
    },
    status: publicStatus(payload.status),
    selectorSyncSupported: payload.selector_sync_supported === true,
    applied: payload.applied && typeof payload.applied === 'object'
      ? { id: payload.applied.id, from: payload.applied.from, to: payload.applied.to, at: payload.applied.at }
      : null,
    clear: payload.clear === true,
    receivedAt: Date.now()
  }
  liveDecisions.delete(key)
  liveDecisions.set(key, record)
  while (liveDecisions.size > MAX_LIVE_SESSIONS) {
    const oldest = liveDecisions.keys().next().value
    liveDecisions.delete(oldest)
    activeStreams.delete(oldest)
    retiredStreams.delete(oldest)
  }
  emitLiveChange()
  return record
}

function sessionInfoMatches(event, waiter) {
  const explicitSource = eventOwner(event)
  const sourceMatches = explicitSource
    ? ownerKey(explicitSource.connectionId, explicitSource.profile) === ownerKey(waiter.connectionId, waiter.profile)
    : waiter.localPrimaryInferred === true &&
      !(typeof event?.connectionId === 'string' && event.connectionId.trim()) &&
      event?.profile === waiter.profile
  const info = event?.payload
  const sessionId = typeof event?.session_id === 'string' ? event.session_id : ''
  return event?.type === SESSION_INFO_EVENT && sourceMatches && info &&
    sessionId === waiter.sessionId && info.reasoning_effort_wire === waiter.target &&
    info.stored_session_id === waiter.conversationId && info.model === waiter.model
}

function publishSessionInfo(event) {
  if (!event || event.replayed === true) return
  rememberSessionIdentity(event)
  for (const [key, waiter] of syncAcks) {
    if (!waiter.settled && sessionInfoMatches(event, waiter)) {
      waiter.settled = true
      waiter.resolve(true)
      waiter.cancelTimeout?.()
      syncAcks.delete(key)
    }
  }
}

function waitForSessionInfo(ctx, details, timeoutMs) {
  const key = liveKey(details.connectionId, details.profile, details.conversationId)
  let resolvePromise
  const promise = new Promise(resolve => { resolvePromise = resolve })
  const waiter = { ...details, resolve: resolvePromise, settled: false, cancelTimeout: null }
  waiter.cancelTimeout = ctx.setTimeout(() => {
    if (!waiter.settled) {
      waiter.settled = true
      syncAcks.delete(key)
      waiter.resolve(false)
    }
  }, Math.max(1, timeoutMs))
  syncAcks.set(key, waiter)
  return { promise, cancel: () => {
    if (!waiter.settled) {
      waiter.settled = true
      syncAcks.delete(key)
      waiter.cancelTimeout?.()
      waiter.resolve(null)
    }
  } }
}

async function syncAppliedEffort(ctx, record) {
  const appliedId = Number(record?.applied?.id)
  const target = record?.applied?.to
  if (!Number.isSafeInteger(appliedId) || appliedId < 1 || !EFFORT_VALUES.has(target) ||
      record.status?.state !== 'decided' || record.status.target !== target) return
  const attemptKey = `${liveKey(record.connectionId, record.profile, record.sessionId)}:${record.streamId}:${appliedId}`
  if (syncAttempts.has(attemptKey)) return
  syncAttempts.add(attemptKey)
  if (syncAttempts.size > MAX_SYNC_ATTEMPTS) syncAttempts.delete(syncAttempts.values().next().value)
  const deadline = Date.now() + 5000

  const setSyncReason = message => {
    const latest = liveFor(record.connectionId, record.profile, record.sessionId)
    if (!latestStillMatchesApplication(record, latest, appliedId, target)) return
    latest.syncReason = message
    emitLiveChange()
  }
  const reportSyncIssue = message => {
    setSyncReason(message)
    if (isFocusedFreshDecision(record)) {
      effortNotice({ kind: 'warning', title: 'Effort applied', message })
    }
  }

  const focused = currentFocusedIdentity()
  const focusedOwnerKey = ownerKey(focused.connectionId, focused.profile)
  const eventOwnerKey = ownerKey(record.connectionId, record.profile)
  if (!record.selectorSyncSupported) {
    if (focused.conversationId === record.sessionId && focusedOwnerKey === eventOwnerKey) {
      reportSyncIssue('Native selector sync is unavailable for isolated turns.')
    }
    return
  }
  if (!focused.sessionId || focused.conversationId !== record.sessionId || focusedOwnerKey !== eventOwnerKey) return
  if (!focused.model || focused.model !== record.route.model) {
    reportSyncIssue('The chat route changed; its reasoning selector was left unchanged.')
    return
  }

  const currentRecord = liveFor(record.connectionId, record.profile, record.sessionId)
  if (!latestStillMatchesApplication(record, currentRecord, appliedId, target)) return

  if (typeof host.profileRoutes !== 'function' || typeof host.requestProfile !== 'function') {
    reportSyncIssue('This Desktop version cannot route a selector update to this chat.')
    return
  }
  let route = null
  try {
    let cancelDeadline
    const routes = await Promise.race([
      host.profileRoutes(),
      new Promise(resolve => { cancelDeadline = ctx.setTimeout(() => resolve(null), Math.max(1, deadline - Date.now())) })
    ])
    cancelDeadline?.()
    let matches = []
    if (Array.isArray(routes)) {
      matches = record.localPrimaryInferred
        ? routes.filter(candidate => candidate?.mode === 'local' && candidate?.profile === record.profile && candidate.primary === true)
        : routes.filter(candidate => candidate?.connectionId === record.connectionId && candidate?.profile === record.profile)
      matches = matches.filter(candidate => typeof candidate?.targetProfile === 'string' && candidate.targetProfile.trim())
      if (record.localPrimaryInferred && matches.length === 0) {
        const localRoutes = routes.filter(candidate => candidate?.mode === 'local' && candidate?.profile === record.profile)
        if (localRoutes.length === 1 && !routes.some(candidate => candidate?.primary === true)) {
          matches = localRoutes.filter(candidate => typeof candidate.targetProfile === 'string' && candidate.targetProfile.trim())
        }
      }
    }
    if (matches.length === 1) route = matches[0]
  } catch {
    route = null
  }
  if (!route) {
    reportSyncIssue('The chat owner could not be resolved safely; its selector was left unchanged.')
    return
  }

  const latestFocus = currentFocusedIdentity()
  const latestRecord = liveFor(record.connectionId, record.profile, record.sessionId)
  if (latestFocus.sessionId !== focused.sessionId || latestFocus.conversationId !== record.sessionId ||
      ownerKey(latestFocus.connectionId, latestFocus.profile) !== eventOwnerKey ||
      latestFocus.model !== record.route.model || !latestRecord ||
      !latestStillMatchesApplication(record, latestRecord, appliedId, target)) return

  const remainingMs = Math.max(1, deadline - Date.now())
  const acknowledgment = waitForSessionInfo(ctx, {
    connectionId: record.connectionId,
    profile: record.profile,
    localPrimaryInferred: record.localPrimaryInferred,
    sessionId: focused.sessionId,
    conversationId: record.sessionId,
    model: record.route.model,
    target
  }, remainingMs)
  try {
    await host.requestProfile(route, 'config.set', {
      key: 'reasoning',
      value: target,
      scope: 'session',
      session_id: focused.sessionId
    }, remainingMs)
  } catch {
    acknowledgment.cancel()
    reportSyncIssue('Hermes could not update this chat’s reasoning selector.')
    return
  }
  const acknowledged = await acknowledgment.promise
  if (!acknowledged) {
    if (acknowledged === false) reportSyncIssue('The reasoning selector did not confirm the update.')
  } else {
    setSyncReason('Selector synced for this chat.')
  }
}

function notifyDecisionOutcome(record) {
  const focus = currentFocusedIdentity()
  if (focus.conversationId !== record.sessionId ||
      ownerKey(focus.connectionId, focus.profile) !== ownerKey(record.connectionId, record.profile)) return
  const entry = record.status
  if (!entry || entry.mode === 'off') return
  if (['failed', 'unsupported'].includes(entry.state)) {
    const reason = outcomeForEntry(entry, entry.mode)
    effortNotice({ kind: 'warning', title: 'Effort not applied', message: `${reason} Request sent unchanged.` })
    return
  }
  const applied = record.applied
  if (entry.state !== 'decided' || !applied ||
      !Number.isSafeInteger(Number(applied.id)) || Number(applied.id) < 1 ||
      typeof applied.from !== 'string' || typeof applied.to !== 'string' ||
      applied.from === applied.to || applied.to !== entry.target) return
  const noticeKey = `${liveKey(record.connectionId, record.profile, record.sessionId)}:${record.streamId}:${applied.id}`
  if (appliedNoticeAttempts.has(noticeKey)) return
  appliedNoticeAttempts.add(noticeKey)
  if (appliedNoticeAttempts.size > MAX_APPLIED_NOTICE_ATTEMPTS) {
    appliedNoticeAttempts.delete(appliedNoticeAttempts.values().next().value)
  }
  effortNotice({ kind: 'info', title: 'Effort changed', message: `${applied.from} → ${applied.to}` })
}

function isFocusedFreshDecision(record) {
  const focus = currentFocusedIdentity()
  return focus.conversationId === record.sessionId &&
    ownerKey(focus.connectionId, focus.profile) === ownerKey(record.connectionId, record.profile) &&
    focus.model === record.route.model
}

function clearSessionAcknowledgments(connectionId, profile, sessionId) {
  const key = liveKey(connectionId, profile, sessionId)
  const waiter = syncAcks.get(key)
  if (waiter && !waiter.settled) {
    waiter.settled = true
    waiter.cancelTimeout?.()
    waiter.resolve(null)
    syncAcks.delete(key)
  }
}

function clearInferredLocalAcknowledgments(event) {
  const profile = typeof event?.profile === 'string' ? event.profile.trim() : ''
  const sessionId = eventConversationId(event?.payload)
  if (!profile || !sessionId || (typeof event?.connectionId === 'string' && event.connectionId.trim())) return
  for (const [key, waiter] of syncAcks) {
    if (waiter.localPrimaryInferred && waiter.profile === profile && waiter.conversationId === sessionId) {
      waiter.settled = true
      waiter.cancelTimeout?.()
      waiter.resolve(null)
      syncAcks.delete(key)
    }
  }
}

function handleDecisionEvent(ctx, event) {
  if (event?.payload?.clear === true) clearInferredLocalAcknowledgments(event)
  const record = rememberDecision(event)
  if (!record) return
  if (record.clear) {
    clearSessionAcknowledgments(record.connectionId, record.profile, record.sessionId)
    return
  }
  if (!isFocusedFreshDecision(record)) return
  notifyDecisionOutcome(record)
  if (record.applied && record.status?.mode !== 'off') {
    void syncAppliedEffort(ctx, record).catch(() => {})
  }
}

function toneFor(mode, isError) {
  if (isError) return 'text-(--ui-warning)'
  if (mode === 'auto') return 'text-(--ui-accent)'
  if (mode === 'once') return 'text-(--ui-text-primary)'
  return 'text-(--ui-text-tertiary)'
}

function useEffortChanges(connectionId, profile) {
  return useQuery({
    queryKey: [ID, 'changes', connectionId, profile],
    queryFn: () => rest('/changes', { timeoutMs: 15000 }),
    refetchInterval: 2000,
    staleTime: 1000,
    retry: 1
  })
}

function entryForConversation(data, conversationId) {
  if (typeof conversationId !== 'string' || !conversationId) return null
  const sessions = Array.isArray(data?.sessions) ? data.sessions : []
  return sessions
    .filter(entry => entry?.conversation_id === conversationId)
    .reduce((best, entry) => {
      const updatedAt = Number(entry?.updated_at) || 0
      return !best || updatedAt >= (Number(best.updated_at) || 0) ? entry : best
    }, null)
}

function effortForConversation(data, conversationId) {
  const latest = entryForConversation(data, conversationId)
  return latest?.state === 'decided' && typeof latest.target === 'string' ? latest.target : 'N/A'
}

function outcomeForEntry(entry, mode) {
  if (!entry) return mode === 'off' ? 'Routing is off.' : 'No request status is available for this chat yet.'
  if (entry.state === 'decided') return null
  if (entry.failure && OUTCOME_HELP[entry.failure]) return OUTCOME_HELP[entry.failure]
  if (entry.state === 'failed') return 'The effort scorer could not classify this request.'
  if (entry.state === 'unsupported') return 'This request has no compatible effort setting.'
  if (entry.state === 'off' || mode === 'off') return 'Routing is off.'
  if (entry.state === 'probing') return 'Effort is being classified.'
  return 'No request status is available for this chat yet.'
}

function routeForConversation(data, conversationId) {
  const latest = entryForConversation(data, conversationId)
  if (!latest) return 'N/A'
  return [latest.provider, latest.model]
    .filter(value => typeof value === 'string' && value.length > 0)
    .join(' · ') || 'N/A'
}

function ownerMatchesActiveBackend(owner, connectionId, profile) {
  const ownerConnectionId = typeof owner?.connectionId === 'string' ? owner.connectionId.trim() : ''
  if (!ownerConnectionId) return false
  const activeConnectionId = typeof connectionId === 'string' ? connectionId.trim() : ''
  const ownerProfile = typeof owner.profile === 'string' ? owner.profile.trim() : ''
  const activeProfile = typeof profile === 'string' ? profile.trim() : ''

  return ownerConnectionId === activeConnectionId &&
    (ownerProfile || 'default') === (activeProfile || 'default')
}

function ChangeNotifications({ query, statusQuery }) {
  const cursor = useRef(null)
  const refetchStatus = statusQuery?.refetch

  useEffect(() => {
    const data = query.data
    if (!data || typeof data.stream_id !== 'string' || !Array.isArray(data.events)) return
    const events = data.events.filter(event => Number.isInteger(event?.id))
    const latestId = events.reduce((max, event) => Math.max(max, event.id), 0)
    const current = cursor.current
    if (!current || current.streamId !== data.stream_id) {
      cursor.current = { streamId: data.stream_id, lastId: latestId }
      if (typeof refetchStatus === 'function') refetchStatus()
      return
    }
    const fresh = events.filter(event => event.id > current.lastId)
    cursor.current = { streamId: data.stream_id, lastId: Math.max(current.lastId, latestId) }
    // This feed is global and has no conversation id. Use it only to refresh
    // the focused chat's status; never derive that chat's effort from the feed.
    // Applied-change toasts come from the matching focused decision event below.
    if (fresh.length > 0 && typeof refetchStatus === 'function') {
      refetchStatus()
    }
  }, [query.data, refetchStatus])

  return null
}

function DecisionNotifications({ query, conversationId, ownerMatchesBackend, connectionId, profile }) {
  const observed = useRef({ conversationId: undefined, initialized: false, latest: new Map() })

  useEffect(() => {
    const selectedConversation = ownerMatchesBackend ? conversationId : null
    if (observed.current.conversationId !== selectedConversation) {
      observed.current = { conversationId: selectedConversation, initialized: false, latest: new Map() }
    }
    const entries = query.data?.sessions
    if (!Array.isArray(entries)) return

    const latest = new Map()
    if (selectedConversation) {
      for (const entry of entries) {
        if (entry?.conversation_id !== selectedConversation) continue
        const route = [entry.provider || '', entry.model || '', entry.api_mode || ''].join('|')
        const prior = latest.get(route)
        if (!prior || (Number(entry.updated_at) || 0) >= (Number(prior.updated_at) || 0)) {
          latest.set(route, entry)
        }
      }
    }

    if (!observed.current.initialized) {
      observed.current.latest = new Map([...latest].map(([route, entry]) => [route, {
        state: entry.state, failure: entry.failure, label: entry.label, target: entry.target
      }]))
      observed.current.initialized = true
      return
    }

    for (const [route, entry] of latest) {
      const previous = observed.current.latest.get(route)
      const current = { state: entry.state, failure: entry.failure, label: entry.label, target: entry.target }
      const actionable = entry.state === 'failed' || entry.state === 'unsupported'
      const wasActionable = previous?.state === 'failed' || previous?.state === 'unsupported'
      const live = liveFor(connectionId, profile, conversationId)
      if (!live && actionable && (!wasActionable || previous.failure !== current.failure ||
          previous.label !== current.label || previous.target !== current.target) && entry.mode !== 'off') {
        const reason = outcomeForEntry(entry, entry.mode)
        effortNotice({
          kind: 'warning',
          title: 'Effort not applied',
          message: `${reason} Request sent unchanged.`
        })
      }
    }
    observed.current.latest = new Map([...latest].map(([route, entry]) => [route, {
      state: entry.state, failure: entry.failure, label: entry.label, target: entry.target
    }]))
  }, [query.data, conversationId, ownerMatchesBackend, connectionId, profile])

  return null
}

async function switchMode(mode, query) {
  try {
    await rest('/mode', { method: 'POST', body: { mode, persist: true }, timeoutMs: 15000 })
    haptic('tap')
    effortNotice({ kind: 'info', message: `Hermes Adaptive Effort mode: ${mode} (persisted)` })
  } catch (err) {
    if (showDesktopPopup) host.notifyError(err, `Could not set effort mode to ${mode}`)
  } finally {
    if (query && typeof query.refetch === 'function') {
      try { await query.refetch() } catch (_ignored) { /* next poll heals */ }
    }
  }
}

function ModeButtons({ mode, query }) {
  return jsx('div', {
    className: 'flex flex-wrap gap-1',
    children: MODES.map(name =>
      jsx(Tip, {
        label: MODE_HELP[name],
        side: 'top',
        children: jsx('button', {
          type: 'button',
          'aria-label': `${name}: ${MODE_HELP[name]}`,
          disabled: name === mode,
          onClick: () => switchMode(name, query),
          className: name === mode
            ? 'rounded-md bg-(--ui-accent) px-2 py-1 text-[0.6875rem] font-medium text-white'
            : 'rounded-md bg-(--ui-fill-secondary) px-2 py-1 text-[0.6875rem] text-(--ui-text-secondary) hover:text-(--ui-text-primary)',
          children: name === mode ? `● ${name}` : name
        })
      }, name)
    )
  })
}

function useConversationHistory(connectionId, profile, conversationId, enabled) {
  return useQuery({
    queryKey: [ID, 'history', connectionId, profile, conversationId],
    queryFn: () => rest(`/history?conversation_id=${encodeURIComponent(conversationId)}&limit=10`,
      { timeoutMs: 15000 }),
    enabled: Boolean(enabled && conversationId),
    refetchInterval: 5000,
    staleTime: 2000,
    retry: 1
  })
}

function historyTime(at) {
  const date = new Date(Number(at) * 1000)
  if (!Number.isFinite(date.getTime())) return 'unknown time'
  return date.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function cacheLabel(verdict) {
  return ({
    compatible: 'Compatible',
    sensitive: 'Cache-sensitive',
    not_verified: 'Not verified'
  })[verdict] || 'Not verified'
}

function AdaptiveEffortDetails({ data, mode, effort, reason, route, syncReason, settings, scorerProvider, scorerModel, credential, gateway, focusedEntry, history, historyError }) {
  const [showActivity, setShowActivity] = useState(false)
  const last = data?.last

  return jsxs('div', {
    className: 'space-y-2 border-t border-(--ui-border) pt-2',
    children: [
      jsxs('div', {
        className: 'space-y-1 text-[0.6875rem]',
        children: [
          jsx('div', { className: 'font-medium', children: 'This chat' }),
          jsx('div', { className: 'text-(--ui-text-secondary)', children: `Effort: ${effort}` }),
          focusedEntry?.decision_type === 'native_choice' && Array.isArray(focusedEntry.choices)
            ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: `Selected from: ${focusedEntry.choices.join(', ')}` })
            : null,
          focusedEntry?.cache_behavior === 'per_message'
            ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: 'Claude changes effort per message; the session selector remains at its initial setting.' })
            : focusedEntry?.cache_behavior === 'top_level_cache_may_reset'
              ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: 'Changing Claude effort between turns may reset prompt caching.' })
              : null,
          effort === 'N/A' && reason
            ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: `Why: ${reason}` })
            : null,
          syncReason
            ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: `Selector: ${syncReason}` })
            : null,
          jsx('div', { className: 'break-all text-(--ui-text-tertiary)', children: `Route: ${route}` })
        ]
      }),
      jsxs('section', {
        className: 'space-y-1 border-t border-(--ui-border) pt-2 text-[0.625rem]',
        children: [
          jsx('div', { className: 'font-medium', children: 'Recent applied changes' }),
          historyError || history?.available === false
            ? jsx('div', { className: 'text-(--ui-text-tertiary)', children: 'History is unavailable for this agent.' })
            : Array.isArray(history?.events) && history.events.length > 0
              ? history.events.map(event => jsxs('div', {
                className: 'border-b border-(--ui-border) py-1 last:border-0',
                children: [
                  jsx('div', { className: 'break-all font-medium text-(--ui-text-secondary)', children: event.model || 'Unknown model' }),
                  jsx('div', { className: 'text-(--ui-text-tertiary)', children: `${historyTime(event.at)} · ${event.from || '?'} → ${event.to || '?'} · ${cacheLabel(event.cache_verdict)}` })
                ]
              }, String(event.id)))
              : jsx('div', { className: 'text-(--ui-text-tertiary)', children: 'No applied changes recorded for this chat.' })
        ]
      }),
      jsxs('div', {
        className: 'space-y-1 text-[0.6875rem]',
        children: [
          jsx('div', { className: 'font-medium', children: 'Scorer' }),
          jsx('div', { className: 'break-all text-(--ui-text-secondary)', children: `${scorerProvider} · ${scorerModel}` }),
          jsx('div', {
            className: 'text-(--ui-text-tertiary)',
            children: `${credential} key · guidance ${settings.classification_instructions_configured ? 'on' : 'off'}`
          }),
          jsx('div', {
            className: 'text-[0.625rem] text-(--ui-text-quaternary)',
            children: mode === 'off'
              ? 'Off: no automatic scoring. Manual probes still send their text.'
              : 'Routing shares recent task text and optional guidance with this scorer.'
          })
        ]
      }),
      jsx('div', {
        className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
        children: `Gateway ${gateway} · key ${data?.credential ? 'present' : data ? 'missing' : '…'}`
      }),
      jsx(Tip, {
        label: 'Counts and latest result across all conversations.',
        side: 'top',
        children: jsx('button', {
          type: 'button',
          'aria-expanded': showActivity,
          'aria-controls': 'effort-activity',
          onClick: () => setShowActivity(!showActivity),
          className: 'text-[0.6875rem] font-medium text-(--ui-text-secondary) hover:text-(--ui-text-primary)',
          children: showActivity ? 'Activity ▾' : 'Activity ▸'
        })
      }),
      showActivity
        ? jsxs('section', {
          id: 'effort-activity',
          className: 'space-y-1 text-[0.625rem] text-(--ui-text-tertiary)',
          children: [
            jsx('div', { children: `Sessions ${data?.counts?.sessions ?? 0} · Requests ${data?.counts?.requests ?? 0} · Probes ${data?.counts?.probes ?? 0}` }),
            jsx('div', { children: `Decided ${data?.counts?.decided ?? 0} · Failed ${data?.counts?.failed ?? 0} · Unsupported ${data?.counts?.unsupported ?? 0}` }),
            last
              ? jsxs('div', {
                className: 'space-y-0.5 pt-1',
                children: [
                  jsx('div', { children: `Last: ${last.state} · effort ${last.target ?? 'N/A'}` }),
                  last.failure
                    ? jsx('div', { children: `Why: ${outcomeForEntry(last, last.mode)}` })
                    : null,
                  jsx('div', { className: 'break-all', children: `Route: ${last.provider ?? 'N/A'} · ${last.model ?? 'N/A'}` }),
                  jsx('div', { className: 'break-all', children: `Scorer: ${last.scorer_provider ?? 'N/A'} · ${last.scorer_model ?? 'N/A'} · ${last.decision_type === 'native_choice' || last.decision_type === 'fixed' ? `choice ${last.label ?? 'N/A'}` : `score ${last.score ?? 'N/A'}`}` })
                ]
              })
              : jsx('div', { className: 'pt-1', children: 'No recent status.' })
          ]
        })
        : null,
      jsx('div', {
        className: 'text-[0.625rem] text-(--ui-text-quaternary)',
        children: 'Fail-open: errors leave requests unchanged. Settings: Capabilities → Plugins or /hae status.'
      })
    ]
  })
}

function AdaptiveEffortChip() {
  const [open, setOpen] = useState(false)
  const [showDetails, setShowDetails] = useState(false)
  const [, setLiveVersion] = useState(0)
  const focusedSessionId = useValue(host.state.focusedSessionId)
  const focusedStoredSessionId = useValue(host.state.focusedStoredSessionId)
  const focusedOwner = useValue(host.state.focusedSessionOwner)
  const activeConnectionId = useValue(host.state.connectionId)
  const activeProfile = useValue(host.state.profile)
  const gateway = useValue(host.state.gateway)
  const focusedConversationId = currentFocusedIdentity().conversationId
  const changesQuery = useEffortChanges(activeConnectionId, activeProfile)
  const query = useQuery({
    queryKey: [ID, 'status', focusedSessionId, focusedStoredSessionId, focusedConversationId,
      focusedOwner?.connectionId, focusedOwner?.profile,
      activeConnectionId, activeProfile],
    queryFn: () => rest('/status', { timeoutMs: 15000 }),
    refetchInterval: 2000,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
    staleTime: 1000,
    retry: 1
  })
  const data = query.data
  if (typeof data?.settings?.show_desktop_popup === 'boolean') {
    showDesktopPopup = data.settings.show_desktop_popup
  }
  const mode = typeof data?.mode === 'string' ? data.mode : null
  const settings = data?.settings || {}
  const scorerProvider = settings.scorer_provider || '…'
  const scorerModel = settings.scorer_model_effective || 'unset'
  const credential = data?.credential_required === false
    ? 'not required'
    : data?.credential ? 'present' : 'missing'
  const ownerMatchesBackend = ownerMatchesActiveBackend(
    focusedOwner, activeConnectionId, activeProfile
  )
  const historyQuery = useConversationHistory(
    activeConnectionId, activeProfile, focusedConversationId, ownerMatchesBackend
  )
  const live = focusedConversationId && focusedOwner
    ? liveFor(focusedOwner.connectionId, focusedOwner.profile, focusedConversationId)
    : null
  const restEntry = ownerMatchesBackend ? entryForConversation(data, focusedConversationId) : null
  const focusedEntry = live ? live.status : restEntry
  const effort = live
    ? live.clear ? 'N/A' : effortForConversation({ sessions: [focusedEntry] }, focusedConversationId)
    : ownerMatchesBackend ? effortForConversation(data, focusedConversationId) : 'N/A'
  const reason = live
    ? live.clear ? 'No request status is available for this chat yet.' : outcomeForEntry(focusedEntry, focusedEntry?.mode || mode)
    : ownerMatchesBackend ? outcomeForEntry(focusedEntry, mode) : 'This chat belongs to a different backend or profile.'
  const route = live
    ? live.clear ? 'N/A' : [live.route.provider, live.route.model, live.route.api_mode].filter(Boolean).join(' · ') || 'N/A'
    : ownerMatchesBackend ? routeForConversation(data, focusedConversationId) : 'N/A'
  const label = `Effort: ${effort}`
  const tone = toneFor(mode, query.isError)

  useEffect(() => subscribeLive(setLiveVersion), [])

  // The manifest setting controls this status-bar chip and its mode popup.
  if (data?.settings?.show_desktop_popup === false) return null

  return jsx(Popover, {
    open,
    onOpenChange: nextOpen => {
      setOpen(nextOpen)
      if (!nextOpen) setShowDetails(false)
    },
    children: jsxs('div', {
      children: [
        jsx(ChangeNotifications, { query: changesQuery, statusQuery: query }),
        jsx(DecisionNotifications, {
          query,
          conversationId: focusedConversationId,
          ownerMatchesBackend,
          connectionId: focusedOwner?.connectionId,
          profile: focusedOwner?.profile
        }),
        jsx(PopoverTrigger, {
          asChild: true,
          children: jsxs('button', {
            type: 'button',
            className: `inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] ${tone} hover:text-(--ui-text-primary)`,
            title: query.isError
              ? 'Scoring backend unavailable — enable the plugin in config (plugins.enabled) and Desktop'
              : `Hermes Adaptive Effort: ${mode || 'loading'} · focused conversation effort: ${effort} · route: ${route} — click to switch`,
            children: [jsx(Codicon, { name: 'zap', className: 'text-[0.75rem]' }), label]
          })
        }),
        jsx(PopoverContent, {
          align: 'end',
          sideOffset: 6,
          className: 'max-h-80 w-64 space-y-2 overflow-y-auto p-3',
          children: jsxs('div', {
            className: 'space-y-3',
            children: [
              query.isError
                ? jsxs('div', {
                  className: 'space-y-1 text-xs',
                  children: [
                    jsx('div', { className: 'font-medium text-(--ui-warning)', children: 'Scoring backend unavailable' }),
                    jsx('div', {
                      className: 'text-(--ui-text-tertiary)',
                      children: 'Enable hermes-adaptive-effort in plugins.enabled and in Capabilities → Plugins.'
                    })
                  ]
                })
                : jsxs('section', {
                  className: 'space-y-1.5',
                  children: [
                    jsxs('div', {
                      className: 'flex items-center justify-between text-xs font-medium',
                      children: [jsx('span', { children: 'Routing mode' }),
                        jsx(Tip, {
                          label: MODE_HELP[mode] || 'Choose how effort is classified and applied.',
                          side: 'top',
                          children: jsx('span', { className: 'text-(--ui-accent)', children: mode || '…' })
                        })]
                    }),
                    jsx(ModeButtons, { mode, query })
                  ]
                }),
              jsx(Tip, {
                label: 'Conversation, scorer, gateway and activity details.',
                side: 'top',
                children: jsx('button', {
                  type: 'button',
                  'aria-label': showDetails ? 'Hide details' : 'Show details',
                  'aria-expanded': showDetails,
                  onClick: () => setShowDetails(!showDetails),
                  className: 'text-[0.6875rem] text-(--ui-text-tertiary) hover:text-(--ui-text-primary)',
                  children: showDetails ? 'Less ▴' : 'More ▾'
                })
              }),
              showDetails
                ? jsx(AdaptiveEffortDetails, {
                  data,
                  mode,
                  effort,
                  reason,
                  route,
                  syncReason: live?.syncReason,
                  settings,
                  scorerProvider,
                  scorerModel,
          credential,
                  gateway,
                  focusedEntry,
                  history: historyQuery.data,
                  historyError: historyQuery.isError
                })
                : null
            ]
          })
        })
      ]
    })
  })
}

export default {
  id: ID,
  name: 'Hermes Adaptive Effort',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    void rest('/status', { timeoutMs: 15000 }).then(data => {
      if (typeof data?.settings?.show_desktop_popup === 'boolean') {
        showDesktopPopup = data.settings.show_desktop_popup
      }
    }).catch(() => {})
    ctx.onEvent(DECISION_EVENT, event => handleDecisionEvent(ctx, event))
    ctx.onEvent(SESSION_INFO_EVENT, publishSessionInfo)
    ctx.onDispose(() => {
      for (const waiter of syncAcks.values()) {
        if (!waiter.settled) {
          waiter.settled = true
          waiter.cancelTimeout?.()
          waiter.resolve(null)
        }
      }
      syncAcks.clear()
      liveDecisions.clear()
      sessionIdentities.clear()
      activeStreams.clear()
      retiredStreams.clear()
      syncAttempts.clear()
      appliedNoticeAttempts.clear()
      emitLiveChange()
    })
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(AdaptiveEffortChip, {})
    })
    ctx.registerMany(MODES.map(mode => ({
      id: `mode-${mode.replace('_', '-')}`,
      area: PALETTE_AREA,
      data: {
        id: `${ID}.mode.${mode}`,
        label: `Adaptive Effort: ${mode}`,
        keywords: ['adaptive effort', 'effort', 'reasoning', mode],
        run: async () => {
          try {
            await rest('/mode', { method: 'POST', body: { mode, persist: true }, timeoutMs: 15000 })
            haptic('tap')
            effortNotice({ kind: 'info', message: `Hermes Adaptive Effort mode: ${mode} (persisted)` })
          } catch (err) {
            if (showDesktopPopup) host.notifyError(err, `Could not set effort mode to ${mode}`)
          }
        }
      }
    })))
  }
}
