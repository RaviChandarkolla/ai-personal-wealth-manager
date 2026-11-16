import { useState, useEffect, useMemo } from 'react'
import axios from 'axios'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import {
  Scale,
  Trophy,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Info,
  Sparkles,
  ArrowLeftRight,
  ExternalLink,
  Newspaper,
} from 'lucide-react'
import FundCombobox, { type SchemeSearchOption } from './FundCombobox'

const API_BASE = 'http://localhost:8000/api'

interface FundCompareProps {
  dataMode: 'synthetic' | 'real'
  initialCodeA?: string | null
  initialCodeB?: string | null
}

const SESSION_SOURCE_LABELS: Record<string, string> = {
  S3: 'Plan Structure & Costs',
  'S3, S8': 'Expense & Fee Discipline',
  S6: 'SEBI Riskometer Classification',
  S7: 'Return & Volatility Consistency',
  'S7, S14': 'Downside & Drawdown Protection',
  S8: 'Fund Size & Track Record',
  'S8, S19': 'Portfolio Concentration',
  S9: 'Category Holding Horizon',
  S10: 'Passive Index Tracking',
  S14: 'Downside & Drawdown Protection',
  S15: 'Hybrid Equity Risk',
  S16: 'Debt Yield & Credit Profile',
  S18: 'Risk-Adjusted Return Ratios',
  S19: 'Fund Manager Stability',
}

function formatRuleSource(ref?: string): string {
  if (!ref) return 'Selection Rule'
  const trimmed = ref.trim()
  if (SESSION_SOURCE_LABELS[trimmed]) {
    return SESSION_SOURCE_LABELS[trimmed]
  }
  return trimmed
    .replace(/\bNotes\s+S\d+(?:\/S\d+)?:\s*/gi, '')
    .replace(/\bS\d+\b/g, (code) => SESSION_SOURCE_LABELS[code] || code)
}

function formatDirection(direction?: string): string {
  if (direction === 'higher') return 'Higher is better'
  if (direction === 'lower') return 'Lower is better'
  if (direction === 'direct_wins') return 'Direct plan preferred'
  if (direction === 'sweet_spot') return '₹500–50,000 Cr sweet spot'
  return 'Informational'
}

function formatCriterionValue(key: string, val: any, unit?: string): string {
  if (val === null || val === undefined) return 'No data'
  if (typeof val === 'number') {
    if (key === 'aum_cr' || key === 'aum_band') {
      return `₹${val.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`
    }
    if (unit === '%') {
      return `${val.toFixed(2)}%`
    }
    if (unit) {
      return `${val.toFixed(2)}${unit.startsWith(' ') ? unit : ` ${unit}`}`
    }
    return val.toFixed(2)
  }
  return String(val)
}

export default function FundCompare({
  dataMode,
  initialCodeA,
  initialCodeB,
}: FundCompareProps) {
  const [fundA, setFundA] = useState<SchemeSearchOption | null>(
    initialCodeA
      ? {
          code: initialCodeA,
          name: initialCodeA,
          category: 'Loading...',
          bucket: 'equity',
          as_of: '',
        }
      : null
  )
  const [fundB, setFundB] = useState<SchemeSearchOption | null>(
    initialCodeB
      ? {
          code: initialCodeB,
          name: initialCodeB,
          category: 'Loading...',
          bucket: 'equity',
          as_of: '',
        }
      : null
  )
  const [includeNarrative, setIncludeNarrative] = useState(true)

  // Hydrate initial codes from /api/compare/schemes if passed via URL
  const hydrateA = useQuery({
    queryKey: ['hydrateScheme', initialCodeA, dataMode],
    enabled: Boolean(initialCodeA),
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/compare/schemes`, {
        params: { q: initialCodeA, plan: 'all', option: 'all', limit: 5, data_mode: dataMode },
      })
      const list = (Array.isArray(res.data) ? res.data : res.data?.schemes || []) as SchemeSearchOption[]
      return list.find((s) => s.code === initialCodeA) || list[0] || null
    },
  })

  const hydrateB = useQuery({
    queryKey: ['hydrateScheme', initialCodeB, dataMode],
    enabled: Boolean(initialCodeB),
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/compare/schemes`, {
        params: { q: initialCodeB, plan: 'all', option: 'all', limit: 5, data_mode: dataMode },
      })
      const list = (Array.isArray(res.data) ? res.data : res.data?.schemes || []) as SchemeSearchOption[]
      return list.find((s) => s.code === initialCodeB) || list[0] || null
    },
  })

  useEffect(() => {
    if (hydrateA.data && (!fundA || fundA.category === 'Loading...')) {
      setFundA(hydrateA.data)
    }
  }, [hydrateA.data])

  useEffect(() => {
    if (hydrateB.data && (!fundB || fundB.category === 'Loading...')) {
      setFundB(hydrateB.data)
    }
  }, [hydrateB.data])

  const compareMutation = useMutation({
    mutationFn: async (payload: { codes: string[]; narrative: boolean }) => {
      const res = await axios.post(`${API_BASE}/compare`, {
        codes: payload.codes,
        narrative: payload.narrative,
        data_mode: dataMode,
      })
      return res.data
    },
    onSuccess: (data) => {
      if (data?.funds?.length === 2) {
        const [pa, pb] = data.funds
        const nextUrl = `/compare?a=${encodeURIComponent(pa.code)}&b=${encodeURIComponent(pb.code)}`
        if (window.location.pathname + window.location.search !== nextUrl) {
          window.history.replaceState({}, '', nextUrl)
        }
      }
    },
  })

  // Auto-run comparison once if both initialCodeA and initialCodeB were provided in URL
  const [didAutoRun, setDidAutoRun] = useState(false)
  useEffect(() => {
    if (
      !didAutoRun &&
      initialCodeA &&
      initialCodeB &&
      initialCodeA !== initialCodeB
    ) {
      setDidAutoRun(true)
      compareMutation.mutate({
        codes: [initialCodeA, initialCodeB],
        narrative: includeNarrative,
      })
    }
  }, [didAutoRun, initialCodeA, initialCodeB])

  const canCompare =
    Boolean(fundA?.code) &&
    Boolean(fundB?.code) &&
    fundA?.code !== fundB?.code &&
    !compareMutation.isPending

  const handleCompare = (e: React.FormEvent) => {
    e.preventDefault()
    if (!fundA?.code || !fundB?.code || fundA.code === fundB.code) return
    compareMutation.mutate({
      codes: [fundA.code, fundB.code],
      narrative: includeNarrative,
    })
  }

  const handleSwap = () => {
    const prevA = fundA
    const prevB = fundB
    setFundA(prevB)
    setFundB(prevA)
    if (prevA?.code && prevB?.code && prevA.code !== prevB.code) {
      compareMutation.mutate({
        codes: [prevB.code, prevA.code],
        narrative: includeNarrative,
      })
    }
  }

  const result = compareMutation.data
  const error = compareMutation.error
    ? (compareMutation.error as any).response?.data?.detail ||
      compareMutation.error.message ||
      'Comparison failed'
    : null

  const profileA = result?.fund_a || result?.funds?.[0]
  const profileB = result?.fund_b || result?.funds?.[1]

  // Prepare Calendar-Year Returns BarChart data
  const calendarChartData = useMemo(() => {
    if (!profileA || !profileB) return []
    const calA = profileA.calendar_returns || {}
    const calB = profileB.calendar_returns || {}
    const years = Array.from(
      new Set([...Object.keys(calA), ...Object.keys(calB)])
    ).sort()
    return years.map((yr) => ({
      year: yr,
      [profileA.code]: calA[yr] ?? calA[Number(yr)] ?? null,
      [profileB.code]: calB[yr] ?? calB[Number(yr)] ?? null,
    }))
  }, [profileA, profileB])

  // Prepare Normalised NAV (Base 100) LineChart data on overlapping dates
  const navChartData = useMemo(() => {
    const histA = profileA?.nav_history || []
    const histB = profileB?.nav_history || []
    if (!histA.length || !histB.length) return []
    const mapA = new Map<string, number>()
    for (const pt of histA) {
      const d = Array.isArray(pt) ? pt[0] : pt.date
      const v = Array.isArray(pt) ? pt[1] : pt.nav
      if (d) mapA.set(d, Number(v))
    }
    const mapB = new Map<string, number>()
    for (const pt of histB) {
      const d = Array.isArray(pt) ? pt[0] : pt.date
      const v = Array.isArray(pt) ? pt[1] : pt.nav
      if (d) mapB.set(d, Number(v))
    }
    const commonDates = Array.from(mapA.keys())
      .filter((d) => mapB.has(d))
      .sort()
    if (commonDates.length < 2) return []

    const baseA = mapA.get(commonDates[0]) || 1
    const baseB = mapB.get(commonDates[0]) || 1

    // Downsample to ~150 points for smooth rendering
    const step = Math.max(1, Math.floor(commonDates.length / 150))
    const sampled: Array<{ date: string; [k: string]: number | string }> = []
    for (let i = 0; i < commonDates.length; i += step) {
      const d = commonDates[i]
      sampled.push({
        date: d,
        [profileA.code]: Number((((mapA.get(d) || baseA) / baseA) * 100).toFixed(2)),
        [profileB.code]: Number((((mapB.get(d) || baseB) / baseB) * 100).toFixed(2)),
      })
    }
    const lastDate = commonDates[commonDates.length - 1]
    if (sampled[sampled.length - 1]?.date !== lastDate) {
      sampled.push({
        date: lastDate,
        [profileA.code]: Number((((mapA.get(lastDate) || baseA) / baseA) * 100).toFixed(2)),
        [profileB.code]: Number((((mapB.get(lastDate) || baseB) / baseB) * 100).toFixed(2)),
      })
    }
    return sampled
  }, [profileA, profileB])

  return (
    <div className="space-y-6">
      {/* Selector Card */}
      <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
          <div>
            <h2 className="text-xl font-semibold text-gray-800 flex items-center gap-2">
              <Scale className="w-5 h-5 text-indigo-600" />
              <span>Head-to-Head Mutual Fund Comparison</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Compare any two mutual funds across 18 deterministic criteria, rolling consistency, Set 2 red-flag checklists, and grounded citations.
            </p>
          </div>

          <label className="inline-flex items-center gap-2 text-xs font-medium text-gray-700 bg-gray-50 px-3 py-2 rounded-lg border border-gray-200 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includeNarrative}
              onChange={(e) => setIncludeNarrative(e.target.checked)}
              className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
            <span>Generate Grounded Summary Verdict</span>
          </label>
        </div>

        <form onSubmit={handleCompare} className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-11 gap-4 items-end">
            <div className="lg:col-span-5">
              <FundCombobox
                label="Fund A"
                value={fundA}
                onChange={setFundA}
                excludeCode={fundB?.code}
                dataMode={dataMode}
                disabled={compareMutation.isPending}
              />
            </div>

            <div className="lg:col-span-1 flex justify-center pb-1">
              <button
                type="button"
                onClick={handleSwap}
                disabled={compareMutation.isPending || (!fundA && !fundB)}
                title="Swap Fund A and Fund B"
                className="p-2.5 rounded-xl border border-gray-200 bg-gray-50 hover:bg-gray-100 text-gray-600 transition disabled:opacity-50"
              >
                <ArrowLeftRight className="w-4 h-4" />
              </button>
            </div>

            <div className="lg:col-span-5">
              <FundCombobox
                label="Fund B"
                value={fundB}
                onChange={setFundB}
                excludeCode={fundA?.code}
                dataMode={dataMode}
                disabled={compareMutation.isPending}
              />
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={!canCompare}
              className="bg-indigo-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-indigo-700 transition disabled:opacity-50 flex items-center gap-2"
            >
              <Scale className="w-4 h-4" />
              <span>{compareMutation.isPending ? 'Computing Comparison...' : 'Compare Funds'}</span>
            </button>
          </div>
        </form>

        {error && (
          <div className="mt-4 p-4 bg-red-50 text-red-700 rounded-lg flex items-start gap-3 text-sm">
            <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
            <p>{error}</p>
          </div>
        )}
      </section>

      {/* Comparison Results */}
      {result && profileA && profileB && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
          {/* Cross-Category / Data Warning Banner */}
          {result.warnings && result.warnings.length > 0 && (
            <div className="p-4 rounded-xl bg-amber-50 border border-amber-200 text-amber-900 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
              <div className="text-xs space-y-1">
                <p className="font-semibold text-sm">Comparison Advisory</p>
                {result.warnings.map((w: string, idx: number) => (
                  <p key={idx}>{w}</p>
                ))}
              </div>
            </div>
          )}

          {/* Winner & Score Banner */}
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
              <div className="flex items-center gap-3">
                <div className="p-3 rounded-full bg-indigo-50 text-indigo-600">
                  <Trophy className="w-6 h-6" />
                </div>
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                    Deterministic Scorecard Ranking
                  </div>
                  <h3 className="text-xl font-bold text-gray-900">
                    {result.winner === 'tie' ||
                    Math.abs(
                      (result.scores[profileA.code] ?? 0) - (result.scores[profileB.code] ?? 0)
                    ) < 1e-6
                      ? 'Tie across evaluated criteria'
                      : `${
                          result.winner_name ||
                          (result.ranking[0] === profileA.code ? profileA.name : profileB.name)
                        } leads overall`}
                  </h3>
                </div>
              </div>
              <span className="text-xs font-mono bg-gray-100 text-gray-600 px-2.5 py-1 rounded">
                Run ID: {result.run_id}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {[
                { label: 'Fund A', profile: profileA, color: 'indigo' },
                { label: 'Fund B', profile: profileB, color: 'emerald' },
              ].map(({ label, profile, color }) => {
                const score = result.scores?.[profile.code] ?? 0
                const isLeader = result.winner !== 'tie' && result.ranking?.[0] === profile.code
                return (
                  <div
                    key={profile.code}
                    className={`p-4 rounded-xl border ${
                      isLeader
                        ? 'border-indigo-300 bg-indigo-50/40'
                        : 'border-gray-200 bg-gray-50/50'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
                          {label} • {profile.code}
                        </span>
                        <h4 className="font-semibold text-gray-900 text-sm mt-0.5">
                          <a
                            href={`/fund/${encodeURIComponent(profile.code)}?data_mode=${encodeURIComponent(dataMode)}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:underline inline-flex items-center gap-1 text-indigo-700"
                          >
                            <span>{profile.name}</span>
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </h4>
                        <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                          <span className="text-[11px] px-2 py-0.5 rounded bg-white border border-gray-200 text-gray-700 font-medium">
                            {profile.category}
                          </span>
                          {profile.plan && (
                            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">
                              {profile.plan}
                            </span>
                          )}
                          {profile.option && (
                            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-semibold">
                              {profile.option}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-2xl font-bold text-gray-900">
                          {score.toFixed(1)}
                          <span className="text-xs font-normal text-gray-500">/100</span>
                        </div>
                        {isLeader && (
                          <span className="inline-block mt-1 px-2 py-0.5 text-[10px] font-bold uppercase rounded bg-indigo-600 text-white">
                            Rank #1
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="mt-3 w-full bg-gray-200 h-2 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          color === 'indigo' ? 'bg-indigo-600' : 'bg-emerald-600'
                        }`}
                        style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Grounded Narrative Verdict */}
            {result.narrative && (
              <div className="mt-5 p-4 rounded-xl bg-indigo-50/60 border border-indigo-100 text-sm text-gray-800 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-indigo-700 uppercase tracking-wider">
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>Grounded Comparison Verdict ({result.narrative_mode || 'template'})</span>
                </div>
                <p className="leading-relaxed">{result.narrative}</p>
              </div>
            )}
          </div>

          {/* Criteria Scorecard Table */}
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <div className="mb-4">
              <h3 className="text-lg font-semibold text-gray-800">
                18-Criterion Deterministic Scorecard
              </h3>
              <p className="text-xs text-gray-500">
                Criteria with missing data or cross-category non-applicability are greyed out and excluded from weight normalisation.
              </p>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-200 text-xs uppercase tracking-wider">
                    <th className="pb-3 font-medium">Criterion</th>
                    <th className="pb-3 font-medium">Direction</th>
                    <th className="pb-3 font-medium text-right">
                      Fund A ({profileA.code})
                    </th>
                    <th className="pb-3 font-medium text-right">
                      Fund B ({profileB.code})
                    </th>
                    <th className="pb-3 font-medium text-center">Winner</th>
                    <th className="pb-3 font-medium text-right">Rule Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(result.criteria || []).map((c: any) => {
                    const key = c.id || c.key
                    const isInactive = c.status !== 'scored'
                    const valA = c.value_a !== undefined ? c.value_a : c.values?.[profileA.code]
                    const valB = c.value_b !== undefined ? c.value_b : c.values?.[profileB.code]
                    return (
                      <tr
                        key={key}
                        className={`${
                          isInactive
                            ? 'bg-gray-50/70 text-gray-400'
                            : 'hover:bg-indigo-50/20 text-gray-800'
                        } transition`}
                      >
                        <td className="py-3 pr-4 font-medium">
                          <div className="flex items-center gap-2">
                            <span>{c.label}</span>
                            {c.status === 'no_data' && (
                              <span className="px-1.5 py-0.5 text-[10px] rounded bg-gray-200 text-gray-600 font-normal">
                                No Data
                              </span>
                            )}
                            {c.status === 'not_applicable' && (
                              <span className="px-1.5 py-0.5 text-[10px] rounded bg-amber-100 text-amber-800 font-normal">
                                N/A
                              </span>
                            )}
                            {c.direction === 'info' && c.status !== 'no_data' && (
                              <span className="px-1.5 py-0.5 text-[10px] rounded bg-blue-50 text-blue-700 font-normal">
                                Info
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-3 pr-4 text-xs text-gray-500">
                          {formatDirection(c.direction)}
                        </td>
                        <td
                          className={`py-3 px-3 text-right font-mono text-xs ${
                            c.winner === 'A' ? 'font-bold text-indigo-700' : ''
                          }`}
                        >
                          {formatCriterionValue(key, valA, c.unit)}
                        </td>
                        <td
                          className={`py-3 px-3 text-right font-mono text-xs ${
                            c.winner === 'B' ? 'font-bold text-emerald-700' : ''
                          }`}
                        >
                          {formatCriterionValue(key, valB, c.unit)}
                        </td>
                        <td className="py-3 px-3 text-center">
                          {c.winner === 'A' ? (
                            <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-indigo-100 text-indigo-800">
                              Fund A
                            </span>
                          ) : c.winner === 'B' ? (
                            <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-100 text-emerald-800">
                              Fund B
                            </span>
                          ) : c.winner === 'tie' ? (
                            <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-gray-100 text-gray-600">
                              Tie
                            </span>
                          ) : (
                            <span className="text-xs text-gray-400">—</span>
                          )}
                        </td>
                        <td className="py-3 pl-3 text-right text-xs text-gray-600">
                          <span className="inline-block px-2 py-0.5 rounded bg-gray-100 text-gray-700 font-medium text-[11px]">
                            {formatRuleSource(c.session_ref || c.source_ref)}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Red-Flag Checklist */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {[
              { title: `Checklist: ${profileA.name}`, code: profileA.code },
              { title: `Checklist: ${profileB.name}`, code: profileB.code },
            ].map(({ title, code }) => {
              const flags = result.checklist?.[code] || []
              return (
                <div
                  key={code}
                  className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 space-y-3"
                >
                  <h3 className="text-base font-semibold text-gray-800 truncate" title={title}>
                    {title}
                  </h3>
                  <div className="space-y-2">
                    {flags.map((flag: any) => {
                      const isFlagged = flag.status === 'flag' || flag.flagged === true
                      const isNoData = flag.status === 'no_data'
                      const isInfo = flag.status === 'info'
                      return (
                        <div
                          key={flag.id || flag.key}
                          className={`p-3 rounded-lg border flex items-start justify-between gap-3 text-xs ${
                            isFlagged
                              ? 'bg-red-50/70 border-red-200 text-red-900'
                              : isNoData
                              ? 'bg-gray-50 border-gray-200 text-gray-500'
                              : isInfo
                              ? 'bg-blue-50/40 border-blue-200 text-gray-800'
                              : 'bg-emerald-50/40 border-emerald-200 text-gray-800'
                          }`}
                        >
                          <div className="flex items-start gap-2">
                            {isFlagged ? (
                              <XCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                            ) : isNoData || isInfo ? (
                              <Info className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5" />
                            ) : (
                              <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0 mt-0.5" />
                            )}
                            <div>
                              <div className="font-semibold">{flag.label}</div>
                              <div className="text-gray-600 mt-0.5">{flag.detail}</div>
                            </div>
                          </div>
                          <span className="text-[10px] font-medium text-gray-600 bg-white/80 px-2 py-0.5 rounded border border-gray-200/70 whitespace-nowrap">
                            {formatRuleSource(flag.session_ref || flag.source_ref)}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Dedicated Separate Section: Real-Time News & Sentiment Comparison */}
          {result.news_comparison && (
            <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 pb-4">
                <div className="flex items-start gap-3">
                  <div className="p-2.5 rounded-lg bg-amber-50 text-amber-700 border border-amber-200 mt-0.5">
                    <Newspaper className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-semibold text-gray-800">
                        Real-Time News & Market Sentiment Comparison
                      </h3>
                      {result.news_comparison.news_winner === 'A' ? (
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-indigo-100 text-indigo-800">
                          News Edge: Fund A ({profileA.code})
                        </span>
                      ) : result.news_comparison.news_winner === 'B' ? (
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800">
                          News Edge: Fund B ({profileB.code})
                        </span>
                      ) : (
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-gray-100 text-gray-700">
                          Neutral / Tie on News Sentiment
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Evaluates recent scheme, AMC, category, and SEBI/RBI regulatory headlines tagged for materiality and sentiment. Kept separate from the 18 quantitative NAV/TER criteria so quantitative rankings remain unconfounded.
                    </p>
                  </div>
                </div>
                <span
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border ${
                    result.news_comparison.live_rss_enabled
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : 'bg-gray-50 text-gray-600 border-gray-200'
                  }`}
                >
                  {result.news_comparison.live_rss_enabled
                    ? 'Live Google News RSS + Regulatory Feed'
                    : 'Curated Regulatory & Category Intelligence'}
                </span>
              </div>

              {result.news_comparison.summary && (
                <div className="p-3.5 rounded-xl bg-amber-50/60 border border-amber-200/80 text-xs text-amber-950">
                  <span className="font-semibold">News & Sentiment Takeaway: </span>
                  {result.news_comparison.summary}
                </div>
              )}

              {/* Side-by-side Fund A vs Fund B News Cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {[
                  {
                    label: 'Fund A',
                    profile: profileA,
                    news: result.news_comparison.fund_a_news,
                    combined: result.news_comparison.combined_score_a,
                    accent: 'indigo',
                  },
                  {
                    label: 'Fund B',
                    profile: profileB,
                    news: result.news_comparison.fund_b_news,
                    combined: result.news_comparison.combined_score_b,
                    accent: 'emerald',
                  },
                ].map(({ label, profile, news, combined, accent }) => {
                  if (!news) return null
                  return (
                    <div
                      key={profile.code}
                      className="p-4 rounded-xl border border-gray-200 bg-gray-50/40 space-y-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <span
                            className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${
                              accent === 'indigo'
                                ? 'bg-indigo-100 text-indigo-800'
                                : 'bg-emerald-100 text-emerald-800'
                            }`}
                          >
                            {label} • {profile.code}
                          </span>
                          <h4 className="font-semibold text-sm text-gray-900 mt-1">
                            {profile.name}
                          </h4>
                          <p className="text-xs text-gray-500">
                            {profile.category} {profile.amc ? `• ${profile.amc}` : ''}
                          </p>
                        </div>
                        <div className="text-right">
                          <div className="text-lg font-bold text-gray-900">
                            {Number(combined ?? news.news_score ?? 50).toFixed(1)}
                            <span className="text-xs font-normal text-gray-500">/100</span>
                          </div>
                          <div className="text-[11px] text-gray-500">
                            News {Number(news.news_score).toFixed(0)} • Sent{' '}
                            {Number(news.sentiment_score).toFixed(0)}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-3 text-[11px] bg-white px-3 py-1.5 rounded-lg border border-gray-200/80">
                        <span className="text-emerald-700 font-semibold">
                          +{news.positive_count} Positive
                        </span>
                        <span className="text-gray-300">|</span>
                        <span className="text-gray-600 font-medium">
                          {news.neutral_count} Neutral
                        </span>
                        <span className="text-gray-300">|</span>
                        <span className="text-red-700 font-semibold">
                          -{news.negative_count} Negative
                        </span>
                        {news.high_materiality_negative > 0 && (
                          <span className="ml-auto px-1.5 py-0.5 rounded bg-red-100 text-red-800 text-[10px] font-bold">
                            {news.high_materiality_negative} High Risk Alert
                          </span>
                        )}
                      </div>

                      <div className="space-y-2">
                        {(news.items || []).slice(0, 4).map((item: any, idx: number) => (
                          <div
                            key={item.id || idx}
                            className="p-2.5 rounded-lg bg-white border border-gray-200/90 text-xs space-y-1"
                          >
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span
                                className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                                  item.sentiment === 'positive'
                                    ? 'bg-emerald-100 text-emerald-800'
                                    : item.sentiment === 'negative'
                                    ? 'bg-red-100 text-red-800'
                                    : 'bg-gray-100 text-gray-700'
                                }`}
                              >
                                {item.sentiment}
                              </span>
                              <span
                                className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                                  item.materiality === 'high'
                                    ? 'bg-amber-100 text-amber-800'
                                    : 'bg-blue-50 text-blue-700'
                                }`}
                              >
                                {item.materiality} materiality
                              </span>
                              <span className="text-[10px] text-gray-400 uppercase">
                                {item.scope}
                              </span>
                              <span className="text-[10px] text-gray-400 font-mono ml-auto">
                                {item.published_at}
                              </span>
                            </div>
                            {item.url ? (
                              <a
                                href={item.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-gray-800 font-medium hover:text-indigo-700 hover:underline inline-flex items-start gap-1"
                              >
                                <span>{item.headline}</span>
                                <ExternalLink className="w-3 h-3 flex-shrink-0 mt-0.5 opacity-60" />
                              </a>
                            ) : (
                              <p className="text-gray-800 font-medium">{item.headline}</p>
                            )}
                            <div className="text-[10px] text-gray-400">Source: {item.source}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Macro & Regulatory Context */}
              {result.news_comparison.macro_news &&
                result.news_comparison.macro_news.length > 0 && (
                  <div className="pt-2 border-t border-gray-100">
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2.5">
                      Shared Macro & Regulatory Context (SEBI • RBI • AMFI)
                    </h4>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      {result.news_comparison.macro_news
                        .slice(0, 3)
                        .map((item: any, idx: number) => (
                          <div
                            key={item.id || idx}
                            className="p-3 rounded-lg bg-gray-50 border border-gray-200 text-xs space-y-1"
                          >
                            <div className="flex items-center gap-1.5">
                              <span
                                className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                                  item.sentiment === 'positive'
                                    ? 'bg-emerald-100 text-emerald-800'
                                    : item.sentiment === 'negative'
                                    ? 'bg-red-100 text-red-800'
                                    : 'bg-gray-200 text-gray-700'
                                }`}
                              >
                                {item.sentiment}
                              </span>
                              <span className="text-[10px] text-gray-500 uppercase font-medium">
                                {item.scope}
                              </span>
                              <span className="text-[10px] text-gray-400 font-mono ml-auto">
                                {item.published_at}
                              </span>
                            </div>
                            <p className="text-gray-800 font-medium line-clamp-2">
                              {item.headline}
                            </p>
                            <div className="text-[10px] text-gray-400">{item.source}</div>
                          </div>
                        ))}
                    </div>
                  </div>
                )}
            </section>
          )}

          {/* Charts: Calendar-Year Returns & Normalised NAV */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Calendar Year Returns BarChart */}
            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
              <h3 className="text-base font-semibold text-gray-800 mb-1">
                Calendar-Year Returns (%)
              </h3>
              <p className="text-xs text-gray-500 mb-4">
                Year-by-year calendar return consistency check
              </p>
              {calendarChartData.length === 0 ? (
                <div className="h-64 flex items-center justify-center text-xs text-gray-400">
                  Insufficient full calendar-year NAV history
                </div>
              ) : (
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={calendarChartData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                      <XAxis dataKey="year" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} unit="%" />
                      <RechartsTooltip
                        formatter={(val: any) =>
                          val != null ? `${Number(val).toFixed(2)}%` : 'N/A'
                        }
                      />
                      <Legend />
                      <Bar
                        dataKey={profileA.code}
                        name={`Fund A (${profileA.code})`}
                        fill="#4f46e5"
                        radius={[4, 4, 0, 0]}
                      />
                      <Bar
                        dataKey={profileB.code}
                        name={`Fund B (${profileB.code})`}
                        fill="#10b981"
                        radius={[4, 4, 0, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            {/* Normalised NAV (Base 100) LineChart */}
            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
              <h3 className="text-base font-semibold text-gray-800 mb-1">
                Normalised NAV Trajectory (Base = 100)
              </h3>
              <p className="text-xs text-gray-500 mb-4">
                Indexed to 100 on the earliest overlapping NAV date
              </p>
              {navChartData.length === 0 ? (
                <div className="h-64 flex items-center justify-center text-xs text-gray-400">
                  No overlapping historical NAV series available
                </div>
              ) : (
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={navChartData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                      <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={30} />
                      <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                      <RechartsTooltip />
                      <Legend />
                      <Line
                        type="monotone"
                        dataKey={profileA.code}
                        name={`Fund A (${profileA.code})`}
                        stroke="#4f46e5"
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey={profileB.code}
                        name={`Fund B (${profileB.code})`}
                        stroke="#10b981"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>

          {/* Not Evaluated Criteria (Requires Holdings Data) */}
          {result.not_evaluated && result.not_evaluated.length > 0 && (
            <div className="bg-amber-50/50 p-5 rounded-xl border border-amber-200/80">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-900 mb-2.5">
                <Info className="w-4 h-4 text-amber-600" />
                <span>Not Evaluated Criteria (Requires Portfolio Holdings Data)</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
                {result.not_evaluated.map((item: any) => (
                  <div
                    key={item.id || item.key}
                    className="p-2.5 rounded-lg bg-white border border-amber-200/70 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2 font-semibold text-gray-800">
                      <span>{item.label}</span>
                      <span className="text-[10px] font-medium text-gray-600 bg-gray-100 px-1.5 py-0.5 rounded">
                        {formatRuleSource(item.session_ref || item.source_ref)}
                      </span>
                    </div>
                    <p className="text-gray-500 text-[11px] mt-0.5">{item.reason}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Disclosures */}
          {result.disclosures && result.disclosures.length > 0 && (
            <div className="bg-gray-100 p-5 rounded-xl border border-gray-200 text-xs text-gray-500">
              <h4 className="font-semibold text-gray-700 mb-2 uppercase tracking-wider">
                Compliance Disclosures
              </h4>
              <ul className="list-disc pl-5 space-y-1">
                {result.disclosures.map((d: string, i: number) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
