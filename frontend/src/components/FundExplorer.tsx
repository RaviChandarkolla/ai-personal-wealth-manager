import { useState, useEffect } from 'react'
import axios from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  FileSearch,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  History,
  ChevronDown,
  ChevronUp,
  Database,
  Sparkles,
  ArrowRight,
} from 'lucide-react'
import FundCombobox, { type SchemeSearchOption } from './FundCombobox'
import FundDetailView from './FundDetailView'

const API_BASE = 'http://localhost:8000/api'

interface FundExplorerProps {
  dataMode: 'synthetic' | 'real'
  initialCode?: string | null
}

interface RefreshReport {
  code: string
  status: 'ok' | 'partial' | 'failed'
  sources: string[]
  include_ter_aum: boolean
  include_news: boolean
  nav_points_before: number
  nav_points_after: number
  nav_date_before: string | null
  nav_date_after: string | null
  latest_nav: number | null
  ter_before: number | null
  ter_after: number | null
  aum_before: number | null
  aum_after: number | null
  news_score: number | null
  sentiment_score: number | null
  changed_fields: string[]
  errors: string[]
  refreshed_at: string
}

interface RefreshLogRecord {
  id: number
  code: string
  refreshed_at: string
  data_mode: string
  status: 'ok' | 'partial' | 'failed'
  sources: string[]
  nav_points_before: number
  nav_points_after: number
  nav_date_before: string | null
  nav_date_after: string | null
  latest_nav: number | null
  ter_before: number | null
  ter_after: number | null
  aum_before: number | null
  aum_after: number | null
  news_score: number | null
  sentiment_score: number | null
  errors: string[]
}

export default function FundExplorer({ dataMode, initialCode }: FundExplorerProps) {
  const queryClient = useQueryClient()

  const [selectedFund, setSelectedFund] = useState<SchemeSearchOption | null>(
    initialCode
      ? {
          code: initialCode,
          name: initialCode,
          category: 'Loading...',
          bucket: 'equity',
          as_of: '',
        }
      : null
  )
  const [includeTerAum, setIncludeTerAum] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [lastReport, setLastReport] = useState<RefreshReport | null>(null)

  const selectedCode = selectedFund?.code || null

  // Hydrate initial code from /api/compare/schemes if passed via URL
  const hydrateQuery = useQuery({
    queryKey: ['hydrateExplorerScheme', initialCode, dataMode],
    enabled: Boolean(initialCode),
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/compare/schemes`, {
        params: { q: initialCode, plan: 'all', option: 'all', limit: 5, data_mode: dataMode },
      })
      const list = (Array.isArray(res.data) ? res.data : res.data?.schemes || []) as SchemeSearchOption[]
      return list.find((s) => s.code === initialCode) || list[0] || null
    },
  })

  useEffect(() => {
    if (hydrateQuery.data && (!selectedFund || selectedFund.category === 'Loading...')) {
      setSelectedFund(hydrateQuery.data)
    }
  }, [hydrateQuery.data])

  // Sync URL with selected scheme
  useEffect(() => {
    const url = selectedCode
      ? `/explore?code=${encodeURIComponent(selectedCode)}`
      : '/explore'
    if (window.location.pathname + window.location.search !== url) {
      window.history.replaceState({}, '', url)
    }
  }, [selectedCode])

  // Clear last refresh banner when switching funds
  useEffect(() => {
    setLastReport(null)
  }, [selectedCode])

  // Sample schemes for quick-pick when no scheme is selected
  const quickPickQuery = useQuery({
    queryKey: ['explorerQuickPicks', dataMode],
    enabled: !selectedCode,
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/compare/schemes`, {
        params: { plan: 'direct', option: 'growth', limit: 6, data_mode: dataMode },
      })
      return (Array.isArray(res.data) ? res.data : res.data?.schemes || []) as SchemeSearchOption[]
    },
  })

  // Refresh log query for selected scheme
  const refreshLogQuery = useQuery({
    queryKey: ['fundRefreshLog', selectedCode],
    enabled: Boolean(selectedCode),
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/funds/${encodeURIComponent(selectedCode!)}/refresh-log`, {
        params: { limit: 10 },
      })
      return (Array.isArray(res.data) ? res.data : res.data?.history || []) as RefreshLogRecord[]
    },
  })

  const refreshMutation = useMutation({
    mutationFn: async (code: string) => {
      const res = await axios.post(`${API_BASE}/funds/${encodeURIComponent(code)}/refresh`, {
        include_ter_aum: includeTerAum,
        include_news: true,
      })
      return res.data as RefreshReport
    },
    onSuccess: (report) => {
      setLastReport(report)
      queryClient.invalidateQueries({ queryKey: ['fundDetail', report.code] })
      queryClient.invalidateQueries({ queryKey: ['fundRefreshLog', report.code] })
      queryClient.invalidateQueries({ queryKey: ['compareSchemes'] })
    },
  })

  const isSyntheticOrDemo =
    dataMode === 'synthetic' || Boolean(selectedCode && selectedCode.toUpperCase().startsWith('DEMO'))

  const refreshError = refreshMutation.error
    ? (refreshMutation.error as any).response?.data?.detail ||
      refreshMutation.error.message ||
      'Failed to refresh fund data'
    : null

  const historyRecords = refreshLogQuery.data || []

  return (
    <div className="space-y-6">
      {/* Picker + Live Refresh Header Card */}
      <section className="bg-white p-6 rounded-xl shadow-xs border border-gray-200 space-y-5">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-indigo-50 text-indigo-600">
              <FileSearch className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900">Fund Explorer & Live Refresh</h2>
              <p className="text-xs text-gray-500">
                Inspect full scheme analytics, Set 2 red-flag hygiene checklist, calendar-year returns & pull live NAV/TER/AUM/news updates
              </p>
            </div>
          </div>

          {selectedCode && (
            <div className="flex flex-wrap items-center gap-3">
              <label
                className={`inline-flex items-center gap-2 text-xs font-medium select-none ${
                  isSyntheticOrDemo || refreshMutation.isPending
                    ? 'text-gray-400 cursor-not-allowed'
                    : 'text-gray-700 cursor-pointer'
                }`}
                title="When checked, also downloads the latest AMFI TER and quarterly AUM spreadsheets and updates this scheme"
              >
                <input
                  type="checkbox"
                  checked={includeTerAum}
                  disabled={isSyntheticOrDemo || refreshMutation.isPending}
                  onChange={(e) => setIncludeTerAum(e.target.checked)}
                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span>Include TER & AUM (slower)</span>
              </label>

              <button
                type="button"
                disabled={isSyntheticOrDemo || refreshMutation.isPending}
                onClick={() => selectedCode && refreshMutation.mutate(selectedCode)}
                title={
                  isSyntheticOrDemo
                    ? 'Live refresh is only available in Real data mode for AMFI schemes'
                    : 'Fetch latest NAV history (mfapi.in), news & sentiment (Google News RSS), and recompute stats'
                }
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-2xs transition disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <RefreshCw
                  className={`w-3.5 h-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`}
                />
                <span>
                  {refreshMutation.isPending
                    ? includeTerAum
                      ? 'Fetching NAV + TER/AUM + News...'
                      : 'Fetching Latest NAV & News...'
                    : 'Fetch Latest'}
                </span>
              </button>

              <button
                type="button"
                onClick={() => setShowHistory((v) => !v)}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-xs font-medium text-gray-700 transition"
              >
                <History className="w-3.5 h-3.5 text-gray-500" />
                <span>Refresh Log ({historyRecords.length})</span>
                {showHistory ? (
                  <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
                ) : (
                  <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
                )}
              </button>
            </div>
          )}
        </div>

        {/* Fund Combobox Picker */}
        <div className="max-w-3xl">
          <FundCombobox
            label="Select Mutual Fund Scheme"
            value={selectedFund}
            onChange={(scheme) => setSelectedFund(scheme)}
            dataMode={dataMode}
          />
        </div>

        {/* Synthetic mode notice */}
        {selectedCode && isSyntheticOrDemo && (
          <div className="flex items-center gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3.5 py-2.5">
            <Database className="w-4 h-4 text-amber-600 shrink-0" />
            <span>
              Live single-fund refresh is disabled in <strong>Synthetic</strong> mode (or for demo schemes). Switch Data mode to <strong>Real</strong> in the top bar to pull live NAVs, TER/AUM, and news for AMFI schemes.
            </span>
          </div>
        )}

        {/* Refresh error banner */}
        {refreshError && (
          <div className="flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3.5 py-2.5">
            <XCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold">Refresh failed:</span> {refreshError}
            </div>
          </div>
        )}

        {/* Live Refresh Result Diff Banner */}
        {lastReport && (
          <div
            className={`rounded-xl border p-4 space-y-3 ${
              lastReport.status === 'ok'
                ? 'bg-emerald-50/70 border-emerald-200'
                : lastReport.status === 'partial'
                  ? 'bg-amber-50/70 border-amber-200'
                  : 'bg-red-50/70 border-red-200'
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                {lastReport.status === 'ok' ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                ) : lastReport.status === 'partial' ? (
                  <AlertTriangle className="w-4 h-4 text-amber-600" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-600" />
                )}
                <span className="text-xs font-bold uppercase tracking-wider text-gray-800">
                  Live Refresh {lastReport.status === 'ok' ? 'Complete' : lastReport.status}
                </span>
                {lastReport.sources.length > 0 && (
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-white border border-gray-200 text-gray-700 font-medium">
                    Sources: {lastReport.sources.join(', ')}
                  </span>
                )}
                {lastReport.changed_fields.length > 0 ? (
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-800 font-semibold">
                    Updated: {lastReport.changed_fields.join(', ')}
                  </span>
                ) : (
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                    Already up to date
                  </span>
                )}
              </div>
              <span className="text-[11px] text-gray-500 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {new Date(lastReport.refreshed_at).toLocaleString()}
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <div className="bg-white/90 rounded-lg p-2.5 border border-gray-200/80">
                <p className="text-[11px] text-gray-500">Latest NAV Date</p>
                <p className="font-semibold text-gray-900 mt-0.5 flex items-center gap-1">
                  <span>{lastReport.nav_date_before || '—'}</span>
                  <ArrowRight className="w-3 h-3 text-gray-400" />
                  <span className="text-indigo-700">{lastReport.nav_date_after || '—'}</span>
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  Points: {lastReport.nav_points_before} → {lastReport.nav_points_after}
                  {lastReport.latest_nav != null ? ` · ₹${lastReport.latest_nav.toFixed(4)}` : ''}
                </p>
              </div>

              <div className="bg-white/90 rounded-lg p-2.5 border border-gray-200/80">
                <p className="text-[11px] text-gray-500">Expense Ratio (TER)</p>
                <p className="font-semibold text-gray-900 mt-0.5 flex items-center gap-1">
                  <span>
                    {lastReport.ter_before != null
                      ? `${lastReport.ter_before.toFixed(2)}%`
                      : '—'}
                  </span>
                  <ArrowRight className="w-3 h-3 text-gray-400" />
                  <span className="text-indigo-700">
                    {lastReport.ter_after != null
                      ? `${lastReport.ter_after.toFixed(2)}%`
                      : '—'}
                  </span>
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  {lastReport.sources.includes('amfi_ter') ? 'Synced from AMFI TER' : 'Unchanged / skipped'}
                </p>
              </div>

              <div className="bg-white/90 rounded-lg p-2.5 border border-gray-200/80">
                <p className="text-[11px] text-gray-500">Scheme AUM</p>
                <p className="font-semibold text-gray-900 mt-0.5 flex items-center gap-1">
                  <span>
                    {lastReport.aum_before != null
                      ? `₹${Math.round(lastReport.aum_before).toLocaleString('en-IN')} Cr`
                      : '—'}
                  </span>
                  <ArrowRight className="w-3 h-3 text-gray-400" />
                  <span className="text-indigo-700">
                    {lastReport.aum_after != null
                      ? `₹${Math.round(lastReport.aum_after).toLocaleString('en-IN')} Cr`
                      : '—'}
                  </span>
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  {lastReport.sources.includes('amfi_aum') ? 'Synced from AMFI AUM' : 'Unchanged / skipped'}
                </p>
              </div>

              <div className="bg-white/90 rounded-lg p-2.5 border border-gray-200/80">
                <p className="text-[11px] text-gray-500">Live News & Sentiment</p>
                <p className="font-semibold text-gray-900 mt-0.5">
                  News Score:{' '}
                  <span className="text-indigo-700">
                    {lastReport.news_score != null ? lastReport.news_score.toFixed(2) : '—'}
                  </span>
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  Sentiment:{' '}
                  {lastReport.sentiment_score != null
                    ? (lastReport.sentiment_score >= 0 ? '+' : '') +
                      lastReport.sentiment_score.toFixed(2)
                    : '—'}
                </p>
              </div>
            </div>

            {lastReport.errors.length > 0 && (
              <div className="text-[11px] text-amber-900 bg-white/80 rounded-lg px-3 py-2 border border-amber-200">
                <span className="font-semibold">Warnings:</span> {lastReport.errors.join(' · ')}
              </div>
            )}
          </div>
        )}

        {/* Collapsible Refresh Log Drawer */}
        {selectedCode && showHistory && (
          <div className="border border-gray-200 rounded-xl overflow-hidden bg-gray-50/60">
            <div className="px-4 py-2.5 bg-gray-100/80 border-b border-gray-200 flex items-center justify-between">
              <span className="text-xs font-semibold text-gray-700">
                Recent Refresh History for #{selectedCode}
              </span>
              <span className="text-[11px] text-gray-500">Last 10 runs</span>
            </div>
            {historyRecords.length === 0 ? (
              <div className="p-4 text-xs text-gray-500 text-center">
                No manual refreshes recorded for this scheme yet.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-gray-200 text-[11px] text-gray-500 bg-white">
                      <th className="py-2 px-3 font-medium">Timestamp</th>
                      <th className="py-2 px-3 font-medium">Status</th>
                      <th className="py-2 px-3 font-medium">Sources</th>
                      <th className="py-2 px-3 font-medium">NAV Date</th>
                      <th className="py-2 px-3 font-medium">NAV Points</th>
                      <th className="py-2 px-3 font-medium">TER / AUM</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 bg-white">
                    {historyRecords.map((rec) => (
                      <tr key={rec.id} className="hover:bg-gray-50">
                        <td className="py-2 px-3 text-gray-700 whitespace-nowrap">
                          {new Date(rec.refreshed_at).toLocaleString()}
                        </td>
                        <td className="py-2 px-3">
                          <span
                            className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase ${
                              rec.status === 'ok'
                                ? 'bg-emerald-100 text-emerald-800'
                                : rec.status === 'partial'
                                  ? 'bg-amber-100 text-amber-800'
                                  : 'bg-red-100 text-red-800'
                            }`}
                          >
                            {rec.status}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-gray-600">
                          {rec.sources.length > 0 ? rec.sources.join(', ') : '—'}
                        </td>
                        <td className="py-2 px-3 text-gray-600 whitespace-nowrap">
                          {rec.nav_date_before || '—'} → {rec.nav_date_after || '—'}
                        </td>
                        <td className="py-2 px-3 text-gray-600">
                          {rec.nav_points_before} → {rec.nav_points_after}
                        </td>
                        <td className="py-2 px-3 text-gray-600">
                          <span>
                            TER: {rec.ter_after != null ? `${rec.ter_after.toFixed(2)}%` : '—'} · AUM:{' '}
                            {rec.aum_after != null ? `₹${Math.round(rec.aum_after)} Cr` : '—'}
                          </span>
                          {rec.errors && rec.errors.length > 0 && (
                            <span className="block text-[10px] text-amber-700 mt-0.5">
                              {rec.errors.join(' · ')}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Main Content: Embedded FundDetailView or Empty State */}
      {selectedCode ? (
        <FundDetailView code={selectedCode} dataMode={dataMode} embedded />
      ) : (
        <section className="bg-white rounded-xl border border-gray-200 p-8 text-center space-y-5 shadow-xs">
          <div className="mx-auto w-12 h-12 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center">
            <Sparkles className="w-6 h-6" />
          </div>
          <div className="max-w-lg mx-auto space-y-1.5">
            <h3 className="text-base font-bold text-gray-900">
              Pick a mutual fund scheme above to explore its full profile
            </h3>
            <p className="text-xs text-gray-500">
              View historical NAV growth, drawdown chart, calendar-year returns, Set 2 red-flag & hygiene checklist, peer percentile ranks, and trigger a live single-fund data refresh.
            </p>
          </div>

          {quickPickQuery.data && quickPickQuery.data.length > 0 && (
            <div className="pt-2 space-y-2.5">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
                Or quick-pick a popular scheme
              </p>
              <div className="flex flex-wrap items-center justify-center gap-2 max-w-3xl mx-auto">
                {quickPickQuery.data.map((scheme) => (
                  <button
                    key={scheme.code}
                    type="button"
                    onClick={() => setSelectedFund(scheme)}
                    className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-gray-50 hover:bg-indigo-50 hover:border-indigo-200 text-xs text-gray-700 hover:text-indigo-700 transition"
                  >
                    <span className="font-medium truncate max-w-xs">{scheme.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white border border-gray-200 text-gray-500">
                      {scheme.category}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
