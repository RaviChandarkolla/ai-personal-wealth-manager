import React, { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import {
  X,
  Search,
  Sliders,
  ChevronDown,
  ChevronRight,
  Sparkles,
  BookOpen,
} from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

export type RuleSetId = 'set1' | 'set2' | 'combined'

interface RulesViewerProps {
  isOpen: boolean
  onClose: () => void
  activeRuleSet: RuleSetId
  onSelectRuleSet?: (set: RuleSetId) => void
  initialRuleId?: string | null
}

interface RuleItem {
  id: string
  title: string
  description: string
  kind: 'hard filter' | 'score' | 'allocation' | 'disclosure'
  group: string
  setting_keys: string[]
  source_ref: string
  value: any
  example: string
  used: boolean
  provenance?: {
    setting: string
    set1_value: any
    set2_value: any
    combined_value: any
    strategy: string
    winner: string
    reason: string
    conflict?: boolean
  }
}

interface RuleGroup {
  name: string
  rules: RuleItem[]
}

interface RulesResponse {
  rule_set: RuleSetId
  label: string
  description: string
  groups: RuleGroup[]
  provenance?: any[]
  provenance_summary?: any[]
}

interface CompareRow {
  id: string
  rule_id: string
  group: string
  title: string
  kind: string
  setting_keys: string[]
  source_ref: string
  set1: any
  set2: any
  combined: any
  set1_value: any
  set2_value: any
  combined_value: any
  has_difference: boolean
  strategy: string
  winner: string
  rationale: string
}

function renderValue(val: any): React.ReactNode {
  if (val === null || val === undefined || val === 'None') {
    return <span className="text-gray-400 italic">None / Not configured</span>
  }
  if (typeof val === 'boolean') {
    return (
      <span
        className={`px-2 py-0.5 rounded text-xs font-semibold ${
          val ? 'bg-emerald-100 text-emerald-800' : 'bg-gray-100 text-gray-600'
        }`}
      >
        {val ? 'Enabled' : 'Disabled'}
      </span>
    )
  }
  if (typeof val === 'number') {
    return <span className="font-semibold text-gray-900">{val}</span>
  }
  if (typeof val === 'string') {
    return <span className="font-semibold text-gray-900">{val}</span>
  }
  if (Array.isArray(val)) {
    if (val.length === 0) return <span className="text-gray-400 italic">None</span>
    return (
      <div className="flex flex-wrap gap-1">
        {val.map((item, idx) => (
          <span
            key={idx}
            className="px-2 py-0.5 rounded-md bg-gray-100 text-gray-800 text-xs font-medium border border-gray-200"
          >
            {typeof item === 'object' ? JSON.stringify(item) : String(item)}
          </span>
        ))}
      </div>
    )
  }
  if (typeof val === 'object') {
    // Check if it's allocation bands (e.g. { Conservative: { equity: [15, 25] } })
    const keys = Object.keys(val)
    if (keys.length > 0 && typeof val[keys[0]] === 'object' && !Array.isArray(val[keys[0]])) {
      return (
        <div className="space-y-1.5 text-xs">
          {Object.entries(val).map(([prof, buckets]: [string, any]) => (
            <div key={prof} className="bg-gray-50 p-2 rounded border border-gray-100">
              <span className="font-bold text-gray-700 capitalize mr-2">{prof}:</span>
              <div className="inline-flex flex-wrap gap-x-3 gap-y-1 mt-0.5">
                {Object.entries(buckets).map(([bkt, rng]: [string, any]) => (
                  <span key={bkt} className="text-gray-600">
                    <span className="capitalize font-medium text-gray-700">{bkt}</span>:{' '}
                    {Array.isArray(rng) ? `${rng[0]}%–${rng[1]}%` : String(rng)}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )
    }

    // Key-value pairs (e.g. expense ceilings, analyst weights)
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 text-xs">
        {Object.entries(val).map(([k, v]) => (
          <div
            key={k}
            className="bg-gray-50 px-2 py-1 rounded border border-gray-100 flex items-center justify-between"
          >
            <span className="text-gray-600 font-medium truncate pr-1" title={k}>
              {k}:
            </span>
            <span className="font-semibold text-gray-900">
              {typeof v === 'number' && v < 1 && v > 0
                ? `${(v * 100).toFixed(1)}%`
                : typeof v === 'number'
                ? v
                : String(v)}
            </span>
          </div>
        ))}
      </div>
    )
  }
  return <span>{String(val)}</span>
}

function KindBadge({ kind }: { kind: string }) {
  let badgeStyle = 'bg-gray-100 text-gray-700 border-gray-200'
  if (kind === 'hard filter') {
    badgeStyle = 'bg-rose-50 text-rose-700 border-rose-200'
  } else if (kind === 'score') {
    badgeStyle = 'bg-indigo-50 text-indigo-700 border-indigo-200'
  } else if (kind === 'allocation') {
    badgeStyle = 'bg-emerald-50 text-emerald-700 border-emerald-200'
  } else if (kind === 'disclosure') {
    badgeStyle = 'bg-amber-50 text-amber-700 border-amber-200'
  }

  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[11px] font-semibold border uppercase tracking-wide ${badgeStyle}`}
    >
      {kind}
    </span>
  )
}

export default function RulesViewer({
  isOpen,
  onClose,
  activeRuleSet,
  onSelectRuleSet,
  initialRuleId,
}: RulesViewerProps) {
  const [tab, setTab] = useState<RuleSetId | 'compare' | 'stocks'>(activeRuleSet)
  const [search, setSearch] = useState('')
  const [showOnlyDiffs, setShowOnlyDiffs] = useState(false)
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({})

  // Fetch rules for set tab
  const rulesQuery = useQuery<RulesResponse>({
    queryKey: ['rules', tab],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/rules?rule_set=${tab}`)
      return res.data
    },
    enabled: isOpen && tab !== 'compare' && tab !== 'stocks',
  })

  // Fetch comparison for compare tab
  const compareQuery = useQuery<CompareRow[]>({
    queryKey: ['rulesCompare'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/rules/compare`)
      return res.data
    },
    enabled: isOpen && tab === 'compare',
  })

  // Fetch stock rules comparison for stocks tab
  const stockRulesQuery = useQuery<any>({
    queryKey: ['stockRulesCompare'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/stocks/rules/compare`)
      return res.data
    },
    enabled: isOpen && tab === 'stocks',
  })

  const toggleGroup = (groupName: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [groupName]: !prev[groupName] }))
  }

  // Filtered groups for Set 1 / Set 2 / Combined tabs
  const filteredGroups = useMemo(() => {
    if (!rulesQuery.data?.groups) return []
    const q = search.trim().toLowerCase()
    if (!q) return rulesQuery.data.groups

    return rulesQuery.data.groups
      .map((g) => ({
        ...g,
        rules: g.rules.filter(
          (r) =>
            r.title.toLowerCase().includes(q) ||
            r.description.toLowerCase().includes(q) ||
            r.id.toLowerCase().includes(q) ||
            r.group.toLowerCase().includes(q)
        ),
      }))
      .filter((g) => g.rules.length > 0)
  }, [rulesQuery.data, search])

  // Filtered compare rows
  const filteredCompareRows = useMemo(() => {
    if (!compareQuery.data) return []
    let rows = compareQuery.data
    if (showOnlyDiffs) {
      rows = rows.filter((r) => r.has_difference)
    }
    const q = search.trim().toLowerCase()
    if (q) {
      rows = rows.filter(
        (r) =>
          r.title.toLowerCase().includes(q) ||
          r.group.toLowerCase().includes(q) ||
          r.id.toLowerCase().includes(q) ||
          r.rationale.toLowerCase().includes(q)
      )
    }
    return rows
  }, [compareQuery.data, showOnlyDiffs, search])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-xs animate-in fade-in duration-200">
      {/* Backdrop click */}
      <div className="absolute inset-0" onClick={onClose} />

      {/* Drawer Container */}
      <div className="relative w-full max-w-4xl bg-white h-full shadow-2xl flex flex-col z-10 animate-in slide-in-from-right duration-300">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-100 text-indigo-700 rounded-lg">
              <Sliders className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900">Mutual Fund Selection Rules</h2>
              <p className="text-xs text-gray-500">
                Transparent rule catalog with live examples, deterministic merge logic & side-by-side comparison
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-700 hover:bg-gray-200 rounded-lg transition"
            aria-label="Close rules viewer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation and Search Bar */}
        <div className="px-6 py-3 border-b border-gray-200 bg-white flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex rounded-lg bg-gray-100 p-1 text-xs font-semibold">
            <button
              type="button"
              onClick={() => setTab('set1')}
              className={`px-3 py-1.5 rounded-md transition ${
                tab === 'set1'
                  ? 'bg-white text-indigo-700 shadow-xs'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Set 1 · Default
            </button>
            <button
              type="button"
              onClick={() => setTab('set2')}
              className={`px-3 py-1.5 rounded-md transition ${
                tab === 'set2'
                  ? 'bg-white text-purple-700 shadow-xs'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Set 2 · Magic of MF
            </button>
            <button
              type="button"
              onClick={() => setTab('combined')}
              className={`px-3 py-1.5 rounded-md transition ${
                tab === 'combined'
                  ? 'bg-white text-emerald-700 shadow-xs'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Combined · Set 1 + 2
            </button>
            <button
              type="button"
              onClick={() => setTab('compare')}
              className={`px-3 py-1.5 rounded-md transition ${
                tab === 'compare'
                  ? 'bg-indigo-600 text-white shadow-xs'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Compare All
            </button>
            <button
              type="button"
              onClick={() => setTab('stocks')}
              className={`px-3 py-1.5 rounded-md transition ${
                tab === 'stocks'
                  ? 'bg-amber-600 text-white shadow-xs'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Stock Rules (Set 1 vs Set 2)
            </button>
          </div>

          <div className="flex items-center gap-2">
            <div className="relative flex-1 sm:w-64">
              <Search className="w-4 h-4 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search rules..."
                className="w-full pl-8 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            {tab === 'compare' && (
              <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer select-none bg-gray-50 px-2.5 py-1.5 rounded-lg border border-gray-200">
                <input
                  type="checkbox"
                  checked={showOnlyDiffs}
                  onChange={(e) => setShowOnlyDiffs(e.target.checked)}
                  className="rounded text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5"
                />
                <span>Differences only</span>
              </label>
            )}
          </div>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {tab === 'stocks' ? (
            stockRulesQuery.isLoading ? (
              <div className="py-12 text-center text-sm text-gray-500">Loading stock rules comparison...</div>
            ) : stockRulesQuery.isError ? (
              <div className="p-4 bg-red-50 text-red-700 rounded-lg text-xs">
                Failed to load stock rules: {String(stockRulesQuery.error)}
              </div>
            ) : (
              <div className="space-y-6">
                <div className="p-4 bg-amber-50/70 rounded-xl border border-amber-200 text-xs space-y-1">
                  <h3 className="text-base font-bold text-gray-900">
                    Direct Equity Fundamental Screening: Set 1 vs Set 2
                  </h3>
                  <p className="text-gray-600">
                    Stock rules are independent of mutual fund rule sets. Set 1 applies quick 1Y equity heuristics; Set 2 (Default) applies the FA Course Notes 10-Pointer Non-Bank / 6-Pointer Bank framework, weighted relative valuation, and governance red flags. Neither rule set imposes a market-cap limit by default.
                  </p>
                </div>

                {/* Side-by-Side Dimensions Table */}
                <div className="bg-white rounded-xl border border-gray-200 shadow-2xs overflow-hidden">
                  <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 font-bold text-xs text-gray-800">
                    Side-by-Side Comparison Across 8 Screening Dimensions
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-gray-100/80 text-gray-700 font-semibold uppercase text-[10px] tracking-wider border-b border-gray-200">
                        <tr>
                          <th className="py-2.5 px-4">Dimension</th>
                          <th className="py-2.5 px-4">Stock Set 1 · Classic Heuristics</th>
                          <th className="py-2.5 px-4">Stock Set 2 · FA Course Notes (Default)</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {(stockRulesQuery.data?.dimensions || []).map((dim: any, idx: number) => (
                          <tr key={idx} className="hover:bg-gray-50/70">
                            <td className="py-2.5 px-4 font-semibold text-gray-900 whitespace-nowrap">
                              {dim.dimension}
                            </td>
                            <td className="py-2.5 px-4 text-gray-700">{dim.set1}</td>
                            <td className="py-2.5 px-4 text-indigo-900 font-medium bg-indigo-50/20">
                              {dim.set2}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Detailed Rule Catalogs */}
                {(['set2', 'set1'] as const).map((rsKey) => {
                  const catalog = stockRulesQuery.data?.rule_sets?.[rsKey]
                  if (!catalog) return null
                  return (
                    <div key={rsKey} className="bg-white rounded-xl border border-gray-200 shadow-2xs overflow-hidden">
                      <div className="px-5 py-3.5 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
                        <div>
                          <span className="font-bold text-sm text-gray-900">
                            {catalog.meta?.label || rsKey.toUpperCase()}
                          </span>
                          <p className="text-xs text-gray-500 mt-0.5">{catalog.pass_policy}</p>
                        </div>
                        <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-100 text-amber-800">
                          {(catalog.rules || []).length} rules
                        </span>
                      </div>
                      <div className="divide-y divide-gray-100">
                        {(catalog.rules || []).map((r: any) => (
                          <div key={r.rule_id} className="p-4 hover:bg-gray-50/50 text-xs space-y-1">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-gray-900">{r.name}</span>
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200">
                                  {r.scope}
                                </span>
                                <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-700 capitalize">
                                  {r.category}
                                </span>
                              </div>
                              <span className="font-mono text-[11px] text-gray-400">{r.rule_id}</span>
                            </div>
                            <p className="text-gray-600">{r.rationale}</p>
                            <div className="text-[11px] font-mono text-indigo-700 bg-indigo-50/50 px-2.5 py-1 rounded border border-indigo-100 inline-block">
                              Threshold: {r.threshold}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          ) : tab !== 'compare' ? (
            /* Set 1, Set 2, or Combined Tab */
            rulesQuery.isLoading ? (
              <div className="py-12 text-center text-sm text-gray-500">Loading rule definitions...</div>
            ) : rulesQuery.isError ? (
              <div className="p-4 bg-red-50 text-red-700 rounded-lg text-xs">
                Failed to load rules: {String(rulesQuery.error)}
              </div>
            ) : (
              <div className="space-y-6">
                {/* Rule Set Banner */}
                <div className="p-4 bg-gray-50 rounded-xl border border-gray-200 flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-base font-bold text-gray-900">{rulesQuery.data?.label}</h3>
                      {onSelectRuleSet && activeRuleSet !== tab && (
                        <button
                          type="button"
                          onClick={() => onSelectRuleSet(tab as RuleSetId)}
                          className="px-2.5 py-1 rounded-md text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700 transition"
                        >
                          Activate this rule set
                        </button>
                      )}
                      {activeRuleSet === tab && (
                        <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-100 text-emerald-800">
                          Active Selection
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-600 mt-1">{rulesQuery.data?.description}</p>
                  </div>
                </div>

                {/* Combined Provenance Highlights */}
                {tab === 'combined' && rulesQuery.data?.provenance_summary && (
                  <div className="bg-emerald-50/60 p-4 rounded-xl border border-emerald-200 text-xs">
                    <div className="flex items-center gap-1.5 font-bold text-emerald-900 mb-2">
                      <Sparkles className="w-4 h-4 text-emerald-700" />
                      <span>Deterministic Merge Principles (8 Strategies Applied)</span>
                    </div>
                    <p className="text-emerald-800 mb-3">
                      Combined merges Set 1 and Set 2 deterministically: filters take the stricter floor or ceiling,
                      scoring weights are blended, overlapping ranges intersect, and lists are unioned.
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px]">
                      {rulesQuery.data.provenance_summary.slice(0, 6).map((p: any, idx: number) => (
                        <div
                          key={idx}
                          className={`p-2 rounded border bg-white ${
                            p.conflict ? 'border-amber-400 bg-amber-50/50' : 'border-emerald-100'
                          }`}
                        >
                          <div className="flex items-center justify-between font-semibold text-gray-800">
                            <span className="truncate">{p.setting}</span>
                            <span
                              className={`px-1.5 py-0.2 rounded text-[10px] ${
                                p.conflict
                                  ? 'bg-amber-100 text-amber-800'
                                  : 'bg-emerald-100 text-emerald-800'
                              }`}
                            >
                              {p.winner}
                            </span>
                          </div>
                          <p className="text-gray-500 mt-0.5">{p.reason}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Groups & Rules */}
                {filteredGroups.length === 0 ? (
                  <div className="py-8 text-center text-sm text-gray-500">
                    No rules found matching &quot;{search}&quot;.
                  </div>
                ) : (
                  filteredGroups.map((group) => {
                    const isCollapsed = !!collapsedGroups[group.name]
                    return (
                      <div
                        key={group.name}
                        className="bg-white rounded-xl border border-gray-200 shadow-2xs overflow-hidden"
                      >
                        {/* Group Header */}
                        <button
                          type="button"
                          onClick={() => toggleGroup(group.name)}
                          className="w-full px-5 py-3.5 bg-gray-50/80 hover:bg-gray-100/80 transition flex items-center justify-between text-left border-b border-gray-200"
                        >
                          <div className="flex items-center gap-2">
                            {isCollapsed ? (
                              <ChevronRight className="w-4 h-4 text-gray-500" />
                            ) : (
                              <ChevronDown className="w-4 h-4 text-gray-500" />
                            )}
                            <span className="font-bold text-sm text-gray-800">{group.name}</span>
                            <span className="text-xs text-gray-400">({group.rules.length} rules)</span>
                          </div>
                        </button>

                        {/* Rules list */}
                        {!isCollapsed && (
                          <div className="divide-y divide-gray-100">
                            {group.rules.map((rule) => {
                              const isTarget = initialRuleId === rule.id
                              return (
                                <div
                                  key={rule.id}
                                  id={`rule-${rule.id}`}
                                  className={`p-5 transition ${
                                    isTarget ? 'bg-indigo-50/40 ring-2 ring-indigo-400' : 'hover:bg-gray-50/50'
                                  } ${!rule.used ? 'opacity-60 bg-gray-50/40' : ''}`}
                                >
                                  {/* Rule Header */}
                                  <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
                                    <div className="flex items-center gap-2">
                                      <h4 className="font-bold text-sm text-gray-900">{rule.title}</h4>
                                      <KindBadge kind={rule.kind} />
                                      {!rule.used && (
                                        <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-gray-200 text-gray-600">
                                          Not Enforced
                                        </span>
                                      )}
                                    </div>
                                    <span className="text-[11px] font-mono text-gray-400 bg-gray-100 px-2 py-0.5 rounded">
                                      {rule.source_ref}
                                    </span>
                                  </div>

                                  <p className="text-xs text-gray-600 mb-3">{rule.description}</p>

                                  {/* Live Value */}
                                  <div className="mb-3">
                                    <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider block mb-1">
                                      Active Threshold / Config:
                                    </span>
                                    <div className="p-2.5 bg-gray-50 rounded-lg border border-gray-200/80">
                                      {renderValue(rule.value)}
                                    </div>
                                  </div>

                                  {/* Live Generated Example */}
                                  {rule.example && (
                                    <div className="p-3 bg-blue-50/70 rounded-lg border border-blue-100 text-xs text-blue-900 flex items-start gap-2.5">
                                      <Sparkles className="w-4 h-4 text-blue-600 flex-shrink-0 mt-0.5" />
                                      <div>
                                        <span className="font-semibold text-blue-800">Live Example: </span>
                                        <span>{rule.example}</span>
                                      </div>
                                    </div>
                                  )}

                                  {/* Provenance for Combined */}
                                  {rule.provenance && (
                                    <div className="mt-2.5 pt-2 border-t border-gray-100 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
                                      <span className="font-semibold text-gray-700">Merge Decision:</span>
                                      <span className="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">
                                        {rule.provenance.strategy}
                                      </span>
                                      <span>&bull;</span>
                                      <span className="font-medium text-gray-600">
                                        Selected: <strong>{rule.provenance.winner}</strong>
                                      </span>
                                      <span>({rule.provenance.reason})</span>
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
            )
          ) : (
            /* Compare All Tab */
            compareQuery.isLoading ? (
              <div className="py-12 text-center text-sm text-gray-500">Loading rule comparison table...</div>
            ) : compareQuery.isError ? (
              <div className="p-4 bg-red-50 text-red-700 rounded-lg text-xs">
                Failed to load comparison: {String(compareQuery.error)}
              </div>
            ) : (
              <div className="bg-white rounded-xl border border-gray-200 shadow-2xs overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-gray-100/80 text-gray-700 font-semibold uppercase text-[10px] tracking-wider border-b border-gray-200">
                      <tr>
                        <th className="py-3 px-4">Rule</th>
                        <th className="py-3 px-3">Kind</th>
                        <th className="py-3 px-3">Set 1 · Default</th>
                        <th className="py-3 px-3">Set 2 · Magic MF</th>
                        <th className="py-3 px-3">Combined</th>
                        <th className="py-3 px-3">Strategy</th>
                        <th className="py-3 px-3">Winner</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {filteredCompareRows.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="py-8 text-center text-gray-400">
                            No rules found matching criteria.
                          </td>
                        </tr>
                      ) : (
                        filteredCompareRows.map((row) => (
                          <tr
                            key={row.id}
                            className={`hover:bg-gray-50/70 transition ${
                              row.has_difference ? 'bg-indigo-50/20' : ''
                            }`}
                          >
                            <td className="py-3 px-4 align-top">
                              <p className="font-bold text-gray-900">{row.title}</p>
                              <p className="text-[10px] text-gray-400">{row.group}</p>
                              <span className="text-[9px] font-mono text-gray-400 block mt-0.5">
                                {row.source_ref}
                              </span>
                            </td>
                            <td className="py-3 px-3 align-top whitespace-nowrap">
                              <KindBadge kind={row.kind} />
                            </td>
                            <td className="py-3 px-3 align-top">
                              <div className="max-w-xs">{renderValue(row.set1)}</div>
                            </td>
                            <td className="py-3 px-3 align-top">
                              <div className="max-w-xs">{renderValue(row.set2)}</div>
                            </td>
                            <td className="py-3 px-3 align-top font-semibold text-gray-900">
                              <div className="max-w-xs">{renderValue(row.combined)}</div>
                            </td>
                            <td className="py-3 px-3 align-top text-gray-600 whitespace-nowrap">
                              <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700 text-[10px] font-medium border border-gray-200">
                                {row.strategy}
                              </span>
                            </td>
                            <td className="py-3 px-3 align-top whitespace-nowrap">
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                                  row.winner.includes('Set 1')
                                    ? 'bg-indigo-100 text-indigo-800'
                                    : row.winner.includes('Set 2')
                                    ? 'bg-purple-100 text-purple-800'
                                    : row.winner.includes('Both') || row.winner.includes('Overlap') || row.winner.includes('Union') || row.winner.includes('Average')
                                    ? 'bg-emerald-100 text-emerald-800'
                                    : 'bg-gray-100 text-gray-700'
                                }`}
                              >
                                {row.winner}
                              </span>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-between text-xs text-gray-500">
          <div className="flex items-center gap-1.5">
            <BookOpen className="w-4 h-4 text-indigo-600" />
            <span>26 selection rules across 7 operational categories</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 bg-gray-200 hover:bg-gray-300 text-gray-800 rounded-lg font-medium transition"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
