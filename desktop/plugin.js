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
  host,
  haptic,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const ID = 'hermes-adaptive-effort'
const MODES = ['off', 'recommend', 'auto', 'cache_safe', 'inject']
const MODE_HELP = {
  off: 'No scoring or effort changes.',
  recommend: 'Scores each new turn and leaves the request unchanged.',
  auto: 'Applies a compatible effort to each new user turn.',
  cache_safe: 'Scores per turn on cache-neutral routes; otherwise pins effort for the session.',
  inject: 'Uses cache-safe routing and can add effort on exact verified routes.'
}
let rest = null

function toneFor(mode, isError) {
  if (isError) return 'text-(--ui-warning)'
  if (mode === 'auto') return 'text-(--ui-accent)'
  if (mode === 'cache_safe' || mode === 'recommend') return 'text-(--ui-text-primary)'
  return 'text-(--ui-text-tertiary)'
}

function useEffortChanges() {
  return useQuery({
    queryKey: [ID, 'changes'],
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

function routeForConversation(data, conversationId) {
  const latest = entryForConversation(data, conversationId)
  if (!latest) return 'N/A'
  return [latest.provider, latest.model]
    .filter(value => typeof value === 'string' && value.length > 0)
    .join(' · ') || 'N/A'
}

function ChangeNotifications({ query }) {
  const cursor = useRef(null)

  useEffect(() => {
    const data = query.data
    if (!data || typeof data.stream_id !== 'string' || !Array.isArray(data.events)) return
    const events = data.events.filter(event => Number.isInteger(event?.id))
    const latestId = events.reduce((max, event) => Math.max(max, event.id), 0)
    const current = cursor.current
    if (!current || current.streamId !== data.stream_id) {
      cursor.current = { streamId: data.stream_id, lastId: latestId }
      return
    }
    const fresh = events.filter(event => event.id > current.lastId)
    cursor.current = { streamId: data.stream_id, lastId: Math.max(current.lastId, latestId) }
    for (const event of fresh) {
      host.notify({
        kind: 'info',
        message: `Reasoning effort changed in a conversation: ${event.from} → ${event.to}`
      })
    }
  }, [query.data])

  return null
}

async function switchMode(mode, query) {
  try {
    await rest('/mode', { method: 'POST', body: { mode, persist: true }, timeoutMs: 15000 })
    haptic('tap')
    host.notify({ kind: 'info', message: `Hermes Adaptive Effort mode: ${mode} (persisted)` })
  } catch (err) {
    host.notifyError(err, `Could not set effort mode to ${mode}`)
  } finally {
    if (query && typeof query.refetch === 'function') {
      try { await query.refetch() } catch (_ignored) { /* next poll heals */ }
    }
  }
}

function ModeButtons({ mode, query, compact }) {
  return jsx('div', {
    className: compact ? 'flex flex-wrap gap-1' : 'grid grid-cols-2 gap-1',
    children: MODES.map(name =>
      jsx('button', {
        key: name,
        type: 'button',
        disabled: name === mode,
        onClick: () => switchMode(name, query),
        className: name === mode
          ? 'rounded-md bg-(--ui-accent) px-2 py-1 text-[0.6875rem] font-medium text-white'
          : 'rounded-md bg-(--ui-fill-secondary) px-2 py-1 text-[0.6875rem] text-(--ui-text-secondary) hover:text-(--ui-text-primary)',
        children: name === mode ? `● ${name}` : name
      }, name)
    )
  })
}

function AdaptiveEffortChip() {
  const [open, setOpen] = useState(false)
  const focusedSessionId = useValue(host.state.focusedSessionId)
  const focusedOwner = useValue(host.state.focusedSessionOwner)
  const activeConnectionId = useValue(host.state.connectionId)
  const activeProfile = useValue(host.state.profile)
  const changesQuery = useEffortChanges()
  const query = useQuery({
    queryKey: [ID, 'status', focusedOwner?.connectionId, focusedOwner?.profile,
      activeConnectionId, activeProfile],
    queryFn: () => rest('/status', { timeoutMs: 15000 }),
    refetchInterval: 2000,
    staleTime: 1000,
    retry: 1
  })
  const data = query.data
  const mode = typeof data?.mode === 'string' ? data.mode : null
  const settings = data?.settings || {}
  const scorerProvider = settings.scorer_provider || '…'
  const scorerModel = settings.scorer_model_effective || 'unset'
  const credential = data?.credential_required === false
    ? 'not required'
    : data?.credential ? 'present' : 'missing'
  const ownerMatchesBackend = focusedOwner?.connectionId === activeConnectionId &&
    focusedOwner?.profile === activeProfile
  const effort = ownerMatchesBackend ? effortForConversation(data, focusedSessionId) : 'N/A'
  const route = ownerMatchesBackend ? routeForConversation(data, focusedSessionId) : 'N/A'
  const label = `Effort: ${effort}`
  const tone = toneFor(mode, query.isError)

  // The manifest setting controls both this status-bar chip and its mode popup.
  // The independent details pane remains available from Desktop's plugin panel.
  if (data?.settings?.show_desktop_popup === false) return null

  return jsx(Popover, {
    open,
    onOpenChange: setOpen,
    children: jsxs('div', {
      children: [
        jsx(ChangeNotifications, { query: changesQuery }),
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
          className: 'max-h-96 w-72 space-y-3 overflow-y-auto p-3',
          children: query.isError
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
            : jsxs('div', {
              className: 'space-y-3',
              children: [
                jsxs('section', {
                  className: 'space-y-1.5',
                  children: [
                    jsxs('div', {
                      className: 'flex items-center justify-between text-xs font-medium',
                      children: [jsx('span', { children: 'Routing mode' }),
                        jsx('span', { className: 'text-(--ui-accent)', children: mode || '…' })]
                    }),
                    jsx('div', {
                      className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                      children: MODE_HELP[mode] || 'Choose how effort is classified and applied.'
                    }),
                    jsx(ModeButtons, { mode, query, compact: true })
                  ]
                }),
                jsxs('section', {
                  className: 'space-y-1 border-t border-(--ui-border) pt-2',
                  children: [
                    jsx('div', {
                      className: 'text-[0.625rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)',
                      children: 'This conversation'
                    }),
                    jsxs('div', {
                      className: 'flex justify-between gap-2 text-xs',
                      children: [jsx('span', { className: 'text-(--ui-text-tertiary)', children: 'Effort' }),
                        jsx('span', { className: 'font-medium', children: effort })]
                    }),
                    jsx('div', {
                      className: 'break-all text-[0.6875rem] text-(--ui-text-tertiary)',
                      children: `Route: ${route}`
                    })
                  ]
                }),
                jsxs('section', {
                  className: 'space-y-1 border-t border-(--ui-border) pt-2',
                  children: [
                    jsx('div', {
                      className: 'text-[0.625rem] font-semibold uppercase tracking-wide text-(--ui-text-quaternary)',
                      children: 'Classifier'
                    }),
                    jsx('div', { className: 'break-all text-xs', children: `${scorerProvider} · ${scorerModel}` }),
                    jsx('div', {
                      className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                      children: `Credential: ${credential} · extra guidance: ${settings.classification_instructions_configured ? 'on' : 'off'}`
                    }),
                    jsx('div', {
                      className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
                      children: mode === 'off'
                        ? 'Automatic routing sends no task text while mode is off. A manual probe still sends its typed text and optional guidance.'
                        : 'Routing sends the bounded latest user text and optional classifier guidance to this scorer.'
                    })
                  ]
                })
              ]
            })
        })
      ]
    })
  })
}

function AdaptiveEffortPane() {
  const gateway = useValue(host.state.gateway)
  const changesQuery = useEffortChanges()
  const query = useQuery({
    queryKey: [ID, 'status-pane'],
    queryFn: () => rest('/status', { timeoutMs: 15000 }),
    refetchInterval: 15000,
    staleTime: 10000,
    retry: 1
  })
  const data = query.data
  const mode = typeof data?.mode === 'string' ? data.mode : '…'
  const scorerProvider = data?.settings?.scorer_provider || '…'
  const scorerModel = data?.settings?.scorer_model_effective || 'unset'
  const last = data?.last || null
  const latestChange = changesQuery.data?.latest || null

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'space-y-0.5',
        children: [
          jsx('div', { className: 'font-medium', children: 'Hermes Adaptive Effort' }),
          jsx('div', {
            className: 'text-xs text-(--ui-text-tertiary)',
            children: `gateway: ${gateway} · credential: ${data?.credential ? 'present' : data ? 'missing' : '…'}` 
          })
        ]
      }),
      query.isError
        ? jsx('div', {
          className: 'text-xs text-(--ui-warning)',
          children: 'Backend unavailable — enable the agent half (plugins.enabled) and this panel in Capabilities → Plugins.'
        })
        : jsxs('div', {
          className: 'space-y-2',
          children: [
            jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: `Mode: ${mode} (persisted on switch)` }),
            jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: `Scorer: ${scorerProvider} · model: ${scorerModel}` }),
            jsx('div', {
              className: 'text-xs text-(--ui-text-secondary)',
              children: latestChange
                ? `latest applied effort (all conversations): ${latestChange.from} → ${latestChange.to}`
                : 'latest applied effort (all conversations): N/A'
            }),
            jsx(ModeButtons, { mode: typeof data?.mode === 'string' ? data.mode : null, query }),
            jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: `sessions=${data?.counts?.sessions ?? 0} requests=${data?.counts?.requests ?? 0} probes=${data?.counts?.probes ?? 0} decided=${data?.counts?.decided ?? 0} failed=${data?.counts?.failed ?? 0} unsupported=${data?.counts?.unsupported ?? 0}`
            }),
            last
              ? jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: `most recent status (all conversations): state=${last.state} label=${last.label ?? 'N/A'} target=${last.target ?? 'N/A'} scorer=${last.scorer_provider ?? 'N/A'} model=${last.scorer_model ?? 'N/A'} score=${last.score ?? 'N/A'}`
              })
              : jsx('div', { className: 'text-xs text-(--ui-text-quaternary)', children: 'last: none' }),
            jsx('div', {
              className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
              children: 'Fail-open: any error leaves the request untouched. Fine-tune in Capabilities → Plugins (gear) or /hae status.'
            })
          ]
        })
    ]
  })
}

export default {
  id: ID,
  name: 'Hermes Adaptive Effort',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(AdaptiveEffortChip, {})
    })
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Adaptive Effort',
      data: { placement: 'right', width: '280px' },
      render: () => jsx(AdaptiveEffortPane, {})
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
            host.notify({ kind: 'info', message: `Hermes Adaptive Effort mode: ${mode} (persisted)` })
          } catch (err) {
            host.notifyError(err, `Could not set effort mode to ${mode}`)
          }
        }
      }
    })))
    ctx.register({
      id: 'status',
      area: PALETTE_AREA,
      data: {
        id: `${ID}.status`,
        label: 'Adaptive Effort: Status',
        keywords: ['adaptive effort', 'effort', 'status'],
        run: () => host.navigate('/hermes-adaptive-effort')
      }
    })
  }
}
