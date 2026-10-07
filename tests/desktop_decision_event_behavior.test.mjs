import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const pluginPath = new URL('../desktop/plugin.js', import.meta.url)
const source = await readFile(pluginPath, 'utf8')
const start = source.indexOf("const ID = 'hermes-adaptive-effort'")
const end = source.indexOf('\nexport default {', start)
assert.ok(start >= 0 && end > start, 'Desktop decision implementation markers must exist')

function makeAtom(value) {
  return { get: () => value, set: next => { value = next } }
}

function makeHarness(options = {}) {
  const timers = []
  const notices = []
  const calls = []
  const state = {
    focusedSessionId: makeAtom(options.sessionId || 'session-1'),
    focusedStoredSessionId: makeAtom(options.conversationId || options.sessionId || 'session-1'),
    focusedSessionOwner: makeAtom(options.owner || { connectionId: 'conn-1', profile: 'work' }),
    connectionId: makeAtom(options.connectionId || 'conn-1'),
    profile: makeAtom(options.profile || 'work'),
    model: makeAtom(options.model || 'model-a'),
    gateway: makeAtom('connected')
  }
  const host = {
    state,
    notify: notice => notices.push(notice),
    notifyError: notice => notices.push(notice),
    profileRoutes: options.profileRoutes || (async () => [{
      connectionId: 'conn-1', profile: 'work', targetProfile: 'work-target'
    }]),
    requestProfile: options.requestProfile || (async (...args) => { calls.push(args) })
  }
  const emptyStatus = options.status || {
    mode: 'auto',
    settings: { show_desktop_popup: true },
    sessions: []
  }
  const jsx = (type, props) => ({ type, props })
  const useQuery = query => query.queryKey[1] === 'status'
    ? { data: emptyStatus, isError: false, refetch: async () => {} }
    : { data: { stream_id: 'rest-stream', events: [] }, isError: false }
  const context = vm.createContext({
    host,
    jsx,
    jsxs: jsx,
    useEffect: () => {},
    useRef: value => ({ current: value }),
    useState: value => [value, () => {}],
    useValue: atom => atom.get(),
    useQuery,
    haptic: () => {},
    PALETTE_AREA: 'palette',
    STATUSBAR_AREAS: { right: 'right' },
    Popover: 'Popover',
    PopoverContent: 'PopoverContent',
    PopoverTrigger: 'PopoverTrigger',
    Codicon: 'Codicon',
    Tip: 'Tip',
    setTimeout,
    clearTimeout,
    console,
    Date,
    Promise,
    Map,
    Set,
    Number,
    String,
    Array,
    JSON
  })
  vm.runInContext(`${source.slice(start, end)}\n;globalThis.api = {
    rememberDecision, handleDecisionEvent, publishSessionInfo, liveFor,
    syncAppliedEffort, AdaptiveEffortChip, setPopup: value => { showDesktopPopup = value },
    setRest: value => { rest = value }, getNotices: () => notices
  }`, context, { filename: 'desktop/plugin.js' })
  context.api.setPopup(emptyStatus.settings?.show_desktop_popup !== false)

  const ctx = {
    setTimeout(callback, ms) {
      const item = { callback, ms, canceled: false }
      timers.push(item)
      return () => { item.canceled = true }
    }
  }
  return { api: context.api, host, state, timers, notices, calls, ctx, context }
}

function decision(overrides = {}) {
  const payload = {
    schema: 'hermes-adaptive-effort.desktop-status.v1',
    runtime_session_id: 'session-1',
    stream_id: 'stream-1',
    revision: 1,
    route: { provider: 'provider-a', model: 'model-a', api_mode: 'responses' },
    status: {
      conversation_id: 'session-1', state: 'decided', target: 'high', mode: 'auto',
      provider: 'provider-a', model: 'model-a', api_mode: 'responses'
    },
    selector_sync_supported: true,
    applied: null
  }
  return {
    type: 'plugin.hermes-adaptive-effort.decision.updated',
    connectionId: 'conn-1',
    profile: 'work',
    payload: { ...payload, ...overrides }
  }
}

function findEffortChip(tree) {
  if (!tree || typeof tree !== 'object') return null
  if (tree.type === 'button' && Array.isArray(tree.props?.children)) {
    const text = tree.props.children.find(child => typeof child === 'string')
    if (typeof text === 'string' && text.startsWith('Effort:')) return text
  }
  const children = tree.props?.children
  for (const child of Array.isArray(children) ? children : [children]) {
    const found = findEffortChip(child)
    if (found) return found
  }
  return null
}

function appliedDecision(overrides = {}) {
  const event = decision({
    applied: { id: 7, from: 'medium', to: 'high', at: 123 },
    ...overrides
  })
  return event
}

function sessionInfo(target = 'high', overrides = {}) {
  return {
    type: 'session.info', connectionId: 'conn-1', profile: 'work',
    session_id: 'session-1',
    payload: { stored_session_id: 'session-1', model: 'model-a', reasoning_effort_wire: target, ...overrides }
  }
}

async function flush() {
  await Promise.resolve()
  await new Promise(resolve => setImmediate(resolve))
  await Promise.resolve()
}

test('fresh focused live status overrides an empty REST response in the rendered chip', () => {
  const h = makeHarness()
  h.api.handleDecisionEvent(h.ctx, decision())
  const rendered = h.api.AdaptiveEffortChip()
  assert.equal(findEffortChip(rendered), 'Effort: high')
})

test('v2 named-choice status is accepted alongside the older v1 event schema', () => {
  const h = makeHarness()
  h.api.handleDecisionEvent(h.ctx, decision({
    schema: 'hermes-adaptive-effort.desktop-status.v2',
    status: {
      ...decision().payload.status,
      score: null,
      decision_type: 'native_choice',
      choices: ['low', 'medium', 'high', 'xhigh', 'max'],
      label: 'xhigh',
      target: 'xhigh',
      cache_behavior: 'per_message'
    },
    selector_sync_supported: false
  }))
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: xhigh')
  assert.equal(h.calls.length, 0)
})

test('stored decision ids match a focused chat whose gateway runtime id is different', () => {
  const storedId = '20261007_123650_2301be'
  const route = { provider: 'openai-codex', model: 'gpt-6-luna', api_mode: 'codex_responses' }
  const h = makeHarness({ sessionId: 'a1b2c3d4', conversationId: storedId, model: route.model })
  const event = decision({ conversation_id: storedId, runtime_session_id: storedId, route,
    status: { ...decision().payload.status, conversation_id: storedId, ...route,
      score: 0.67, target: 'medium' },
    applied: { id: 1, from: 'max', to: 'medium', at: 123 }, selector_sync_supported: false })
  h.api.handleDecisionEvent(h.ctx, event)
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: medium')
  assert.equal(h.notices[0].message, 'max → medium')

  h.state.focusedStoredSessionId.set('another-chat')
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: N/A')
})

test('REST status matches the stored conversation id instead of the gateway runtime id', () => {
  const h = makeHarness({ sessionId: 'runtime-8hex', conversationId: 'session-1', status: {
    mode: 'auto', settings: { show_desktop_popup: true }, sessions: [decision().payload.status,
      { conversation_id: 'another-chat', state: 'decided', target: 'low', updated_at: 999 }]
  } })
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: high')
})

test('legacy events still carry a stored id even though their field says runtime_session_id', () => {
  const h = makeHarness({ sessionId: 'runtime-8hex', conversationId: 'session-1' })
  h.api.handleDecisionEvent(h.ctx, decision())
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: high')
})

test('session.info follows a rotated stored id and isolates mappings by owner', () => {
  const h = makeHarness({ sessionId: 'runtime-8hex', conversationId: 'lineage-root' })
  const info = sessionInfo('high', { stored_session_id: 'session-1' })
  info.session_id = 'runtime-8hex'
  h.api.publishSessionInfo({ ...info, connectionId: 'another-owner', payload: {
    ...info.payload, stored_session_id: 'unrelated-chat'
  } })
  h.api.handleDecisionEvent(h.ctx, decision())
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: N/A')
  h.api.publishSessionInfo(info)
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: high')

  h.api.publishSessionInfo({ ...info, replayed: true, payload: {
    ...info.payload, stored_session_id: 'stale-chat'
  } })
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: high')
  h.api.publishSessionInfo({ ...info, payload: { ...info.payload, stored_session_id: 'new-tip' } })
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: N/A')
})

test('selector sync uses the gateway runtime id and requires acknowledgment of the stored id', async () => {
  const h = makeHarness({ sessionId: 'runtime-8hex', conversationId: 'session-1' })
  h.host.requestProfile = async (...args) => {
    h.calls.push(args)
    const info = sessionInfo()
    info.session_id = 'runtime-8hex'
    h.api.publishSessionInfo({ ...info, payload: { ...info.payload, stored_session_id: 'wrong-chat' } })
  }
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0][2].session_id, 'runtime-8hex')
  assert.equal(h.calls[0][2].scope, 'session')
  assert.notEqual(h.api.liveFor('conn-1', 'work', 'session-1').syncReason, 'Selector synced for this chat.')
  const ack = sessionInfo()
  ack.session_id = 'runtime-8hex'
  h.api.publishSessionInfo(ack)
  await flush()
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason, 'Selector synced for this chat.')
  assert.equal(h.notices[0].message, 'medium → high')
})

test('a runtime rebind during route resolution prevents a write to the old runtime', async () => {
  let resolveRoutes
  const h = makeHarness({ sessionId: 'runtime-old', conversationId: 'session-1',
    profileRoutes: () => new Promise(resolve => { resolveRoutes = resolve }) })
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  h.state.focusedSessionId.set('runtime-new')
  resolveRoutes([{ connectionId: 'conn-1', profile: 'work', targetProfile: 'work-target' }])
  await flush()
  assert.equal(h.calls.length, 0)
})

test('local primary events without connectionId resolve stored and runtime ids separately', async () => {
  const h = makeHarness({ sessionId: 'runtime-local', conversationId: 'session-1',
    connectionId: 'local', owner: { connectionId: 'local', profile: 'work' },
    profileRoutes: async () => [{ connectionId: 'local-registry', mode: 'local',
      primary: true, profile: 'work', targetProfile: 'work-target' }] })
  h.host.requestProfile = async (...args) => {
    h.calls.push(args)
    const info = sessionInfo()
    delete info.connectionId
    info.session_id = 'runtime-local'
    h.api.publishSessionInfo(info)
  }
  const event = appliedDecision()
  delete event.connectionId
  h.api.handleDecisionEvent(h.ctx, event)
  await flush()
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: high')
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0][2].session_id, 'runtime-local')
  assert.equal(h.api.liveFor('local', 'work', 'session-1').syncReason, 'Selector synced for this chat.')
})

test('a stored conversation clear cancels an acknowledgment for a distinct runtime id', async () => {
  const h = makeHarness({ sessionId: 'runtime-8hex', conversationId: 'session-1' })
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  assert.equal(h.calls.length, 1)
  h.api.handleDecisionEvent(h.ctx, decision({ conversation_id: 'session-1',
    clear: true, status: null, applied: null, revision: 2 }))
  await flush()
  assert.ok(h.timers.every(timer => timer.canceled))
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: N/A')
  assert.equal(h.notices.filter(notice => notice.kind === 'warning').length, 0)
})

test('duplicate, replayed, stale, and retired-stream events are rejected; profiles stay separate', () => {
  const h = makeHarness()
  const first = decision()
  assert.ok(h.api.rememberDecision(first))
  assert.equal(h.api.rememberDecision(first), null)
  assert.equal(h.api.rememberDecision({ ...first, replayed: true,
    payload: { ...first.payload, revision: 2 } }), null)
  assert.equal(h.api.rememberDecision({ ...first,
    payload: { ...first.payload, revision: 0 } }), null)

  const replacement = decision({ stream_id: 'stream-2', revision: 1,
    status: { ...first.payload.status, target: 'low' } })
  assert.equal(h.api.rememberDecision(replacement).status.target, 'low')
  assert.equal(h.api.rememberDecision({ ...first,
    payload: { ...first.payload, revision: 99 } }), null)

  const otherProfile = { ...replacement, profile: 'personal', payload: {
    ...replacement.payload, stream_id: 'personal-stream', status: {
      ...replacement.payload.status, target: 'medium'
    }
  } }
  assert.equal(h.api.rememberDecision(otherProfile).status.target, 'medium')
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').status.target, 'low')
  assert.equal(h.api.liveFor('conn-1', 'personal', 'session-1').status.target, 'medium')
})

test('a background decision never syncs later when that chat receives focus', async () => {
  const h = makeHarness()
  const background = appliedDecision({ runtime_session_id: 'background-session' })
  background.payload.status.conversation_id = 'background-session'
  h.api.handleDecisionEvent(h.ctx, background)
  await flush()
  h.state.focusedSessionId.set('background-session')
  h.state.focusedStoredSessionId.set('background-session')
  await flush()
  assert.equal(h.calls.length, 0)
})

test('off, no-op, and child-isolated decisions never call the native selector RPC', async () => {
  const h = makeHarness()
  const off = appliedDecision({ status: {
    ...decision().payload.status, mode: 'off'
  } })
  h.api.handleDecisionEvent(h.ctx, off)
  const noOp = decision({ revision: 2, applied: null })
  h.api.handleDecisionEvent(h.ctx, noOp)
  const isolated = appliedDecision({ revision: 3, selector_sync_supported: false })
  h.api.handleDecisionEvent(h.ctx, isolated)
  await flush()
  assert.equal(h.calls.length, 0)
})

test('focused applied effort uses the exact owner route and session-scoped config.set, then confirms', async () => {
  const exactRoute = { connectionId: 'conn-1', profile: 'work', targetProfile: 'work-target' }
  const h = makeHarness({ profileRoutes: async () => [
    { connectionId: 'conn-1', profile: 'personal', targetProfile: 'wrong-profile' },
    { connectionId: 'other-connection', profile: 'work', targetProfile: 'wrong-connection' },
    exactRoute
  ] })
  h.host.requestProfile = async (...args) => {
    h.calls.push(args)
    h.api.publishSessionInfo(sessionInfo())
  }
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()

  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0][0], exactRoute)
  assert.equal(h.calls[0][1], 'config.set')
  assert.deepEqual({ ...h.calls[0][2] }, {
    key: 'reasoning', value: 'high', scope: 'session', session_id: 'session-1'
  })
  assert.equal(h.notices.length, 1)
  assert.equal(h.notices[0].title, 'Effort changed')
  assert.equal(h.notices[0].message, 'medium → high')
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason,
    'Selector synced for this chat.')
})

test('hiding the popup suppresses notices without disabling selector sync', async () => {
  const h = makeHarness({ status: {
    mode: 'auto', settings: { show_desktop_popup: false }, sessions: []
  } })
  h.host.requestProfile = async (...args) => {
    h.calls.push(args)
    h.api.publishSessionInfo(sessionInfo())
  }
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()

  assert.equal(h.calls.length, 1)
  assert.equal(h.notices.length, 0)
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason,
    'Selector synced for this chat.')
})

test('a missing session.info acknowledgment reports timeout without retrying', async () => {
  const h = makeHarness()
  h.host.requestProfile = async (...args) => { h.calls.push(args) }
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  assert.equal(h.calls.length, 1)
  const pending = h.timers.find(timer => !timer.canceled && timer.ms > 1000)
  assert.ok(pending, 'session acknowledgment timeout should be pending')
  pending.callback()
  await flush()
  assert.equal(h.calls.length, 1)
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason,
    'The reasoning selector did not confirm the update.')
})

test('focus or model route changing before the scoped write prevents the RPC', async () => {
  let resolveRoutes
  const h = makeHarness({ profileRoutes: () => new Promise(resolve => { resolveRoutes = resolve }) })
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  h.state.model.set('model-b')
  resolveRoutes([{ connectionId: 'conn-1', profile: 'work', targetProfile: 'work-target' }])
  await flush()
  assert.equal(h.calls.length, 0)
})

test('clear boundary events with empty route fields clear focused live state', () => {
  const h = makeHarness()
  h.api.rememberDecision(decision())
  const clear = decision({ clear: true, status: null,
    route: { provider: '', model: '', api_mode: '' }, applied: null, revision: 2 })
  h.api.handleDecisionEvent(h.ctx, clear)
  const live = h.api.liveFor('conn-1', 'work', 'session-1')
  assert.equal(live.clear, true)
  assert.equal(live.status, null)
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: N/A')
})
