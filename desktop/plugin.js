// hindsight-curator — Desktop "Memory curation" page (SDK-only, plain ESM).
// Data via ctx.rest → dashboard/plugin_api.py. Degrades with a clear error when
// the agent half is disabled (backend not mounted).
import React from 'react'
import { Button, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

const h = React.createElement

function useRest(ctx) {
  return React.useCallback(async (path, opts) => {
    if (!ctx?.rest) throw new Error('ctx.rest unavailable — enable the hindsight-curator agent plugin')
    const res = await ctx.rest(path, opts)
    if (res == null) throw new Error('empty response from hindsight-curator backend')
    return typeof res === 'string' ? JSON.parse(res) : res
  }, [ctx])
}

function ResultBadge({ result }) {
  if (!result) return null
  const tone =
    result === 'ok' ? 'text-emerald-600' :
    result === 'not_found' ? 'text-amber-600' :
    'text-red-500'
  return h('span', { className: `text-[11px] font-medium uppercase ${tone}` }, result)
}

function PlanList({ plans, selectedId, onSelect }) {
  if (!plans.length) {
    return h('div', { className: 'text-xs text-muted-foreground' },
      'No stored plans yet. Create one from chat with /memory-curate plan …')
  }
  return h('div', { className: 'space-y-1' },
    ...plans.map(p => {
      const active = p.plan_id === selectedId
      return h('button', {
        key: p.plan_id,
        type: 'button',
        onClick: () => onSelect(p.plan_id),
        className: `w-full rounded border px-2 py-1.5 text-left text-xs transition-colors ${
          active
            ? 'border-foreground/40 bg-muted/50'
            : 'border-border/60 hover:bg-muted/30'
        }`,
      },
        h('div', { className: 'flex items-center gap-2' },
          h('span', { className: 'font-mono text-[11px]' }, p.plan_id),
          h('span', { className: 'ml-auto uppercase tracking-wide text-muted-foreground' }, p.status)),
        h('div', { className: 'mt-0.5 truncate text-muted-foreground' },
          p.query || '(no query)', ' · ', p.reason || ''),
        h('div', { className: 'text-muted-foreground' },
          `${p.count ?? 0} candidate(s)`, p.kept ? ` · ${p.kept} kept` : ''))
    }))
}

function CandidateRow({ item, checked, onToggle, result }) {
  const locked = !!item.locked
  return h('label', {
    className: `flex items-start gap-2 rounded border border-border/50 px-2 py-1.5 text-xs ${
      locked ? 'opacity-70' : ''
    }`,
  },
    h('input', {
      type: 'checkbox',
      className: 'mt-0.5',
      checked: locked ? true : checked,
      disabled: locked,
      onChange: e => onToggle(item.id, e.target.checked),
    }),
    h('div', { className: 'min-w-0 flex-1' },
      h('div', { className: 'flex items-center gap-2' },
        h('span', { className: 'font-mono text-[11px]' }, item.id),
        item.kept
          ? h('span', { className: 'text-[10px] uppercase tracking-wide text-muted-foreground' }, 'whitelist')
          : null,
        h('span', { className: 'ml-auto' }, h(ResultBadge, { result }))),
      h('div', { className: 'text-muted-foreground' },
        item.type ? `${item.type} · ` : '', item.excerpt || '(empty)')))
}

function Page({ ctx }) {
  const rest = useRest(ctx)
  const [plans, setPlans] = React.useState([])
  const [selectedId, setSelectedId] = React.useState(null)
  const [detail, setDetail] = React.useState(null)
  const [selected, setSelected] = React.useState({})
  const [results, setResults] = React.useState({})
  const [error, setError] = React.useState(null)
  const [busy, setBusy] = React.useState(false)
  const [statusLine, setStatusLine] = React.useState('')

  const refreshPlans = React.useCallback(async () => {
    setError(null)
    try {
      const data = await rest('/plans?limit=50')
      if (!data?.ok) throw new Error(data?.error || 'failed to load plans')
      setPlans(data.plans || [])
      return data.plans || []
    } catch (e) {
      setError(String(e?.message || e))
      return []
    }
  }, [rest])

  const loadPlan = React.useCallback(async (planId) => {
    if (!planId) {
      setDetail(null)
      setSelected({})
      setResults({})
      return
    }
    setBusy(true)
    setError(null)
    try {
      const data = await rest(`/plans/${encodeURIComponent(planId)}`)
      if (!data?.ok) throw new Error(data?.error || 'failed to load plan')
      setDetail(data)
      const next = {}
      for (const it of data.items || []) {
        if (!it.locked) next[it.id] = true
      }
      setSelected(next)
      const prior = {}
      for (const ev of data.apply_events || []) {
        prior[ev.memory_id] = ev.result
      }
      setResults(prior)
      setStatusLine('')
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }, [rest])

  React.useEffect(() => {
    refreshPlans().then(list => {
      if (list.length && !selectedId) setSelectedId(list[0].plan_id)
    })
  }, [refreshPlans]) // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => { loadPlan(selectedId) }, [selectedId, loadPlan])

  const onToggle = (id, checked) => {
    setSelected(s => ({ ...s, [id]: checked }))
  }

  const onApply = async () => {
    if (!selectedId) return
    const ids = Object.keys(selected).filter(id => selected[id])
    setBusy(true)
    setError(null)
    try {
      const data = await rest(`/plans/${encodeURIComponent(selectedId)}/apply`, {
        method: 'POST',
        body: { ids },
      })
      if (!data?.ok) throw new Error(data?.error || 'apply failed')
      const map = {}
      for (const r of data.results || []) map[r.id] = r.result
      setResults(map)
      setStatusLine(
        `Apply: invalidated ${data.invalidated ?? 0}` +
        `, not_found ${data.not_found ?? 0}, failed ${data.failed ?? 0}`)
      await refreshPlans()
      await loadPlan(selectedId)
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const onRestore = async () => {
    if (!selectedId) return
    setBusy(true)
    setError(null)
    try {
      const data = await rest(`/plans/${encodeURIComponent(selectedId)}/restore`, {
        method: 'POST',
        body: {},
      })
      if (!data?.ok) throw new Error(data?.error || 'restore failed')
      const map = {}
      for (const r of data.results || []) map[r.id] = r.result
      setResults(map)
      setStatusLine(
        `Restore: ok ${data.restored ?? 0}` +
        `, not_found ${data.not_found ?? 0}, failed ${data.failed ?? 0}`)
      await refreshPlans()
      await loadPlan(selectedId)
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const onRefresh = async () => {
    setBusy(true)
    try {
      await refreshPlans()
      if (selectedId) await loadPlan(selectedId)
    } finally {
      setBusy(false)
    }
  }

  const applyDisabled = busy || !selectedId || !Object.values(selected).some(Boolean) ||
    (detail && detail.status === 'applied')

  return h('div', { className: 'flex h-full flex-col gap-3 overflow-auto p-4 text-sm text-foreground' },
    h('div', { className: 'flex items-center gap-2' },
      h('div', null,
        h('div', { className: 'text-base font-semibold' }, 'Memory curation'),
        h('div', { className: 'text-xs text-muted-foreground' },
          'Review dry-run plans, apply a selected subset, or restore.')),
      h('span', { className: 'flex-1' }),
      h(Button, { size: 'sm', variant: 'outline', disabled: busy, onClick: onRefresh },
        busy ? '…' : 'Refresh')),
    error
      ? h('div', { className: 'rounded border border-red-500/40 p-2 text-xs text-red-500' }, error,
          h('div', { className: 'mt-1 text-muted-foreground' },
            'Enable the hindsight-curator agent plugin in this profile, then Rescan Desktop plugins.'))
      : null,
    statusLine
      ? h('div', { className: 'rounded border border-border/60 bg-muted/30 px-2 py-1 text-xs' }, statusLine)
      : null,
    h('div', { className: 'grid min-h-0 flex-1 gap-3 md:grid-cols-[280px_1fr]' },
      h('div', { className: 'space-y-2' },
        h('div', { className: 'text-[11px] font-semibold uppercase tracking-wide text-muted-foreground' },
          'Plans'),
        h(PlanList, {
          plans,
          selectedId,
          onSelect: setSelectedId,
        })),
      h('div', { className: 'space-y-2' },
        !detail
          ? h('div', { className: 'text-xs text-muted-foreground' }, 'Select a plan to review candidates.')
          : h(React.Fragment, null,
              h('div', { className: 'space-y-1' },
                h('div', { className: 'font-mono text-xs' }, detail.plan_id),
                h('div', { className: 'text-xs text-muted-foreground' },
                  `status ${detail.status} · query ${detail.query || '(none)'}`),
                h('div', { className: 'text-xs' }, detail.reason || '')),
              h('div', { className: 'flex flex-wrap gap-2' },
                h(Button, {
                  size: 'sm',
                  disabled: applyDisabled,
                  onClick: onApply,
                }, 'Apply selected'),
                h(Button, {
                  size: 'sm',
                  variant: 'outline',
                  disabled: busy || !selectedId,
                  onClick: onRestore,
                }, 'Restore')),
              h('div', { className: 'space-y-1' },
                ...(detail.items || []).map(item => h(CandidateRow, {
                  key: item.id,
                  item,
                  checked: !!selected[item.id],
                  onToggle,
                  result: results[item.id],
                }))))))
}

export default {
  id: 'hindsight-curator',
  name: 'Hindsight Curator',
  defaultEnabled: false,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/memory-curation' },
        render: () => h(Page, { ctx }),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/memory-curation', label: 'Memory curation', codicon: 'archive' },
      },
    ])
  },
}
