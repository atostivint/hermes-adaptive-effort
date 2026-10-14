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
  const queries = []
  const useQuery = query => {
    queries.push(query)
    if (query.queryKey[1] === 'status') return { data: emptyStatus, isError: false, refetch: async () => {} }
    if (query.queryKey[1] === 'eval') return { data: options.evalData, isError: false }
    return { data: { stream_id: 'rest-stream', events: [] }, isError: false }
  }
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
    console: options.console || console,
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
    syncAppliedEffort, AdaptiveEffortChip, ScorerEvaluation, evaluationRows, setPopup: value => { showDesktopPopup = value },
    setRest: value => { rest = value }, getNotices: () => notices,
    applyStatusSettings, getDebugLog: () => debugLog
  }`, context, { filename: 'desktop/plugin.js' })
  context.api.setPopup(emptyStatus.settings?.show_desktop_popup !== false)

  const ctx = {
    setTimeout(callback, ms) {
      const item = { callback, ms, canceled: false }
      timers.push(item)
      return () => { item.canceled = true }
    }
  }
  return { api: context.api, host, state, timers, notices, calls, ctx, context, queries }
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

function collect(tree, predicate, out = []) {
  if (!tree || typeof tree !== 'object') return out
  // Expand function components (ModeButtons) the way a renderer would.
  if (typeof tree.type === 'function') return collect(tree.type(tree.props), predicate, out)
  if (predicate(tree)) out.push(tree)
  const children = tree.props?.children
  for (const child of Array.isArray(children) ? children : [children]) collect(child, predicate, out)
  return out
}

function textsOf(tree) {
  return collect(tree, node => typeof node.props?.children === 'string')
    .map(node => node.props.children)
}

function modeButtons(tree, ariaStart) {
  return collect(tree, node => node.type === 'button' && String(node.props['aria-label'] || '').startsWith(ariaStart))
}

const scopedStatus = (conversation, extra = {}) => ({
  mode: 'auto', default_mode: 'auto', settings: { show_desktop_popup: true }, sessions: [],
  conversation: { conversation_id: 'session-1', mode: null, effective_mode: 'auto', source: 'default', ...conversation },
  ...extra
})

test('popup shows This conversation and All conversations sections', () => {
  const h = makeHarness({ status: scopedStatus({}) })
  const texts = textsOf(h.api.AdaptiveEffortChip())
  assert.ok(texts.includes('This conversation'))
  assert.ok(texts.includes('All conversations'))
  assert.ok(!texts.includes('Routing mode'))
})

test('conversation buttons POST scope=conversation with the stored conversation id', async () => {
  const h = makeHarness({ status: scopedStatus({}) })
  const posts = []
  h.api.setRest(async (path, options) => { posts.push([path, options]) })
  const tree = h.api.AdaptiveEffortChip()
  const off = modeButtons(tree, 'off:')
  assert.equal(off.length, 2, 'one conversation button and one global button')
  off[0].props.onClick()
  await flush()
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0][1].body)), { mode: 'off', scope: 'conversation', conversation_id: 'session-1' })
  assert.equal(posts[0][0], '/mode')
  off[1].props.onClick()
  await flush()
  assert.deepEqual(JSON.parse(JSON.stringify(posts[1][1].body)), { mode: 'off', persist: true })
})

test('Use default posts mode default for the conversation and is active without an override', async () => {
  const h = makeHarness({ status: scopedStatus({}) })
  const posts = []
  h.api.setRest(async (path, options) => { posts.push(options.body) })
  const [useDefault] = modeButtons(h.api.AdaptiveEffortChip(), 'Use default:')
  assert.equal(useDefault.props.disabled, true)
  assert.equal(useDefault.props.children, '● Use default')

  const overridden = makeHarness({ status: scopedStatus({ mode: 'off', effective_mode: 'off', source: 'conversation' }) })
  overridden.api.setRest(async (path, options) => { posts.push(options.body) })
  const [button] = modeButtons(overridden.api.AdaptiveEffortChip(), 'Use default:')
  assert.equal(button.props.disabled, false)
  button.props.onClick()
  await flush()
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0])), { mode: 'default', scope: 'conversation', conversation_id: 'session-1' })
})

test('chip label gets a this-chat suffix only for a conversation override', () => {
  const h = makeHarness({ status: scopedStatus({ mode: 'off', effective_mode: 'off', source: 'conversation' }) })
  h.api.handleDecisionEvent(h.ctx, decision())
  assert.equal(findEffortChip(h.api.AdaptiveEffortChip()), 'Effort: high · off (this chat)')
  const plain = makeHarness({ status: scopedStatus({}) })
  plain.api.handleDecisionEvent(plain.ctx, decision())
  assert.equal(findEffortChip(plain.api.AdaptiveEffortChip()), 'Effort: high')
})

test('older status payloads without conversation fields keep the global UI', () => {
  const h = makeHarness()
  h.api.handleDecisionEvent(h.ctx, decision())
  const tree = h.api.AdaptiveEffortChip()
  assert.equal(findEffortChip(tree), 'Effort: high')
  const texts = textsOf(tree)
  assert.ok(texts.includes('Routing mode'))
  assert.ok(!texts.includes('This conversation'))
  assert.equal(modeButtons(tree, 'off:').length, 1)
})

function textOf(tree) {
  if (tree === null || tree === undefined) return ''
  if (typeof tree === 'string') return tree
  if (Array.isArray(tree)) return tree.map(textOf).join('|')
  return textOf(tree.props?.children)
}

test('scorer evaluation section lists one row per scorer and says when there is none', () => {
  const h = makeHarness()
  assert.match(textOf(h.api.ScorerEvaluation({ query: { data: { evaluations: [] } } })), /No evaluation yet/)
  const text = textOf(h.api.ScorerEvaluation({ query: { data: { evaluations: [
    { provider: 'jev', scorer_model: 'jev-latest', choices: ['low', 'medium', 'high'], items: 66,
      accuracy: 0.5, p95_ms: 812.4, run_at: '2026-10-14T10:00:00Z', failures: {}, corpus_version: 'v' },
    { provider: 'bogus-row-without-fields' }, null
  ] } } }))
  assert.match(text, /Scorer evaluation/)
  assert.match(text, /jev · jev-latest/)
  assert.match(text, /50% · p95 812 ms · 66 items · 2026-10-14 · low\/medium\/high/)
})

test('the evaluation is fetched only while the popup is open and is gated by show_desktop_popup', () => {
  const h = makeHarness({ evalData: { evaluations: [] } })
  h.api.AdaptiveEffortChip()
  const evalQuery = h.queries.find(query => query.queryKey[1] === 'eval')
  assert.ok(evalQuery, 'chip registers the evaluation query')
  assert.equal(evalQuery.enabled, false)
  assert.ok(String(evalQuery.queryFn).includes("rest('/eval'"))
  const hidden = makeHarness({ status: { mode: 'auto', settings: { show_desktop_popup: false }, sessions: [] } })
  assert.equal(hidden.api.AdaptiveEffortChip(), null)
})

const UNRESOLVED_OWNER = 'The chat owner could not be resolved safely; its selector was left unchanged.'
const ambiguousRoutes = async () => [
  { connectionId: 'conn-1', profile: 'work', targetProfile: 'work-target-a' },
  { connectionId: 'conn-1', profile: 'work', targetProfile: 'work-target-b' }
]

test('debug off keeps the unresolved-owner notice unchanged and records nothing', async () => {
  const debugLines = []
  const h = makeHarness({ profileRoutes: ambiguousRoutes, console: { debug: (...args) => debugLines.push(args) } })
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  assert.equal(h.calls.length, 0)
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason, UNRESOLVED_OWNER)
  assert.equal(h.notices.at(-1).message, UNRESOLVED_OWNER)
  assert.equal(h.api.getDebugLog().length, 0)
  assert.equal(debugLines.length, 0)
})

test('debug on names an ambiguous owner with counts only, never identifiers', async () => {
  const debugLines = []
  const h = makeHarness({ profileRoutes: ambiguousRoutes, console: { debug: (...args) => debugLines.push(args) } })
  h.api.applyStatusSettings({ settings: { debug: true } })
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  const expected = `${UNRESOLVED_OWNER} [debug: route_ambiguous inferred=false routes=2 local=0 primary=0 owner=2 targets=2]`
  assert.equal(h.calls.length, 0)
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason, expected)
  assert.equal(h.notices.at(-1).message, expected)
  const log = h.api.getDebugLog()
  assert.equal(log.length, 1)
  assert.equal(log[0].code, 'route_ambiguous')
  assert.equal(debugLines.length, 1)
  const serialized = JSON.stringify([...log, expected])
  for (const identifier of ['conn-1', 'work', 'session-1', 'model-a', 'provider-a']) {
    assert.ok(!serialized.includes(identifier), `debug output leaked ${identifier}`)
  }
})

test('debug on separates a route-list timeout, an error and a missing owner', async () => {
  const quiet = { debug: () => {} }
  const timeout = makeHarness({ profileRoutes: () => new Promise(() => {}), console: quiet })
  timeout.api.applyStatusSettings({ settings: { debug: true } })
  timeout.api.handleDecisionEvent(timeout.ctx, appliedDecision())
  await flush()
  timeout.timers.find(timer => !timer.canceled && timer.ms > 1000).callback()
  await flush()
  assert.equal(timeout.api.getDebugLog().at(-1).code, 'routes_timeout')

  const failing = makeHarness({ profileRoutes: async () => { throw new Error('boom') }, console: quiet })
  failing.api.applyStatusSettings({ settings: { debug: true } })
  failing.api.handleDecisionEvent(failing.ctx, appliedDecision())
  await flush()
  assert.equal(failing.api.getDebugLog().at(-1).code, 'routes_error')

  const missing = makeHarness({ profileRoutes: async () => [
    { connectionId: 'other', profile: 'work', targetProfile: 'x' }
  ], console: quiet })
  missing.api.applyStatusSettings({ settings: { debug: true } })
  missing.api.handleDecisionEvent(missing.ctx, appliedDecision())
  await flush()
  assert.equal(missing.api.getDebugLog().at(-1).code, 'route_none')
  assert.match(missing.api.liveFor('conn-1', 'work', 'session-1').syncReason,
    /\[debug: route_none inferred=false routes=1 local=0 primary=0 owner=0 targets=0\]$/)
})

test('debug on records a successful sync without changing its message', async () => {
  const h = makeHarness({ console: { debug: () => {} } })
  h.api.applyStatusSettings({ settings: { debug: true } })
  h.host.requestProfile = async (...args) => {
    h.calls.push(args)
    h.api.publishSessionInfo(sessionInfo())
  }
  h.api.handleDecisionEvent(h.ctx, appliedDecision())
  await flush()
  assert.equal(h.calls.length, 1)
  assert.equal(h.api.liveFor('conn-1', 'work', 'session-1').syncReason, 'Selector synced for this chat.')
  assert.equal(h.api.getDebugLog().at(-1).code, 'synced')
})
