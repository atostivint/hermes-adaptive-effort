/** Jev-Auto Effort — desktop toggle + live status for Hermes Desktop.
 *
 * Unified-package half of the agent plugin: install location is
 * `~/.hermes/plugins/jev-auto-effort/desktop/plugin.js` (copied app-level by the
 * main process). Plain ESM, no build step: only `@hermes/plugin-sdk`, `react`
 * and `react/jsx-runtime` resolve.
 *
 * Backend is the sibling `dashboard/plugin_api.py` (`/api/plugins/jev-auto-effort/`),
 * itself a thin wrapper around the agent half's command/middleware. Everything
 * fails open: backend disabled or unreachable renders `Effort: —`, actions toast.
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

const ID = 'jev-auto-effort'
const MODES = ['off', 'recommend', 'auto', 'cache_safe']
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
        message: `Jev changed reasoning effort: ${event.from} → ${event.to}`
      })
    }
  }, [query.data])

  return null
}

async function switchMode(mode, query) {
  try {
    await rest('/mode', { method: 'POST', body: { mode, persist: true }, timeoutMs: 15000 })
    haptic('tap')
    host.notify({ kind: 'info', message: `Jev-Auto Effort mode: ${mode} (persisted)` })
  } catch (err) {
    host.notifyError(err, `Could not set Jev mode to ${mode}`)
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

function JevChip() {
  const [open, setOpen] = useState(false)
  const changesQuery = useEffortChanges()
  const query = useQuery({
    queryKey: [ID, 'status'],
    queryFn: () => rest('/status', { timeoutMs: 15000 }),
    refetchInterval: 2000,
    staleTime: 1000,
    retry: 1
  })
  const data = query.data
  const mode = typeof data?.mode === 'string' ? data.mode : null
  const latest = changesQuery.data?.latest
  const effort = typeof data?.last?.target === 'string'
    ? data.last.target
    : typeof latest?.to === 'string' ? latest.to : '—'
  const label = `Effort: ${effort}`
  const tone = toneFor(mode, query.isError)

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
              ? 'Jev backend unavailable — enable the plugin in config (plugins.enabled) and Desktop'
              : `Jev-Auto Effort: ${mode || 'loading'} · latest chosen effort: ${effort} — click to switch`,
            children: [jsx(Codicon, { name: 'zap', className: 'text-[0.75rem]' }), label]
          })
        }),
        jsx(PopoverContent, {
          align: 'end',
          sideOffset: 6,
          className: 'w-64 space-y-2 p-3',
          children: query.isError
            ? jsxs('div', {
              className: 'space-y-1 text-xs',
              children: [
                jsx('div', { className: 'font-medium text-(--ui-warning)', children: 'Jev backend unavailable' }),
                jsx('div', {
                  className: 'text-(--ui-text-tertiary)',
                  children: 'Enable jev-auto-effort in plugins.enabled and in Capabilities → Plugins.'
                })
              ]
            })
            : jsxs('div', {
              className: 'space-y-2',
              children: [
                jsx('div', { className: 'text-xs font-medium', children: `Mode: ${mode || '…'}` }),
                jsx(ModeButtons, { mode, query, compact: true }),
                jsx('div', {
                  className: 'text-[0.625rem] text-(--ui-text-quaternary)',
                  children: `sessions=${data?.counts?.sessions ?? 0} probes=${data?.counts?.probes ?? 0} credential=${data?.credential ? 'present' : 'missing'}`
                })
              ]
            })
        })
      ]
    })
  })
}

function JevPane() {
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
  const last = data?.last || null
  const latestChange = changesQuery.data?.latest || null

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'space-y-0.5',
        children: [
          jsx('div', { className: 'font-medium', children: 'Jev-Auto Effort' }),
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
            jsx('div', {
              className: 'text-xs text-(--ui-text-secondary)',
              children: latestChange
                ? `last applied effort: ${latestChange.from} → ${latestChange.to}`
                : 'last applied effort: —'
            }),
            jsx(ModeButtons, { mode: typeof data?.mode === 'string' ? data.mode : null, query }),
            jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: `sessions=${data?.counts?.sessions ?? 0} requests=${data?.counts?.requests ?? 0} probes=${data?.counts?.probes ?? 0} decided=${data?.counts?.decided ?? 0} failed=${data?.counts?.failed ?? 0} unsupported=${data?.counts?.unsupported ?? 0}`
            }),
            last
              ? jsx('div', {
                className: 'text-xs text-(--ui-text-tertiary)',
                children: `last: state=${last.state} label=${last.label ?? '—'} target=${last.target ?? '—'} score=${last.score ?? '—'}`
              })
              : jsx('div', { className: 'text-xs text-(--ui-text-quaternary)', children: 'last: none' }),
            jsx('div', {
              className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
              children: 'Fail-open: any error leaves the request untouched. Fine-tune in Capabilities → Plugins (gear) or /jev-auto-effort status.'
            })
          ]
        })
    ]
  })
}

export default {
  id: ID,
  name: 'Jev Auto Effort',
  defaultEnabled: false,
  register(ctx) {
    rest = ctx.rest
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(JevChip, {})
    })
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Jev Effort',
      data: { placement: 'right', width: '280px' },
      render: () => jsx(JevPane, {})
    })
    ctx.registerMany(MODES.map(mode => ({
      id: `mode-${mode.replace('_', '-')}`,
      area: PALETTE_AREA,
      data: {
        id: `${ID}.mode.${mode}`,
        label: `Jev Effort: ${mode}`,
        keywords: ['jev', 'effort', 'reasoning', mode],
        run: async () => {
          try {
            await rest('/mode', { method: 'POST', body: { mode, persist: true }, timeoutMs: 15000 })
            haptic('tap')
            host.notify({ kind: 'info', message: `Jev-Auto Effort mode: ${mode} (persisted)` })
          } catch (err) {
            host.notifyError(err, `Could not set Jev mode to ${mode}`)
          }
        }
      }
    })))
    ctx.register({
      id: 'status',
      area: PALETTE_AREA,
      data: {
        id: `${ID}.status`,
        label: 'Jev Effort: Status',
        keywords: ['jev', 'effort', 'status'],
        run: () => host.navigate('/jev-auto-effort')
      }
    })
  }
}
