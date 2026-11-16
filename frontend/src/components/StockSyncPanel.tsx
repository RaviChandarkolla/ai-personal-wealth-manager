import { useState, useEffect } from 'react'
import axios from 'axios'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, CheckCircle2, AlertTriangle, RotateCcw, Filter, Layers } from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

interface StockSyncPanelProps {
  compact?: boolean
  onSynced?: () => void
}

export default function StockSyncPanel({ compact = false, onSynced }: StockSyncPanelProps) {
  const queryClient = useQueryClient()
  const [minMarketCapInput, setMinMarketCapInput] = useState<string>('')
  const [minTradedValueInput, setMinTradedValueInput] = useState<string>('')
  const [forceSync, setForceSync] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [feedbackMsg, setFeedbackMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const statusQuery = useQuery({
    queryKey: ['stocksSyncStatus'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/stocks/sync/status`)
      return res.data
    },
    refetchInterval: activeJobId ? 1500 : false,
  })

  const jobQuery = useQuery({
    queryKey: ['stocksSyncJob', activeJobId],
    queryFn: async () => {
      if (!activeJobId) return null
      const res = await axios.get(`${API_BASE}/stocks/sync/${activeJobId}`)
      return res.data
    },
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const st = query.state.data?.status
      return st === 'running' ? 1000 : false
    },
  })

  useEffect(() => {
    if (jobQuery.data?.status === 'completed') {
      setActiveJobId(null)
      queryClient.invalidateQueries({ queryKey: ['stocksSyncStatus'] })
      queryClient.invalidateQueries({ queryKey: ['stocksList'] })
      onSynced?.()
    }
  }, [jobQuery.data?.status, queryClient, onSynced])

  const syncMutation = useMutation({
    mutationFn: async (mode: 'full' | 'retry_failed' | 'rescreen') => {
      setFeedbackMsg(null)
      setErrorMsg(null)
      const minCap = minMarketCapInput.trim() !== '' ? Number(minMarketCapInput) : undefined
      const minTv = minTradedValueInput.trim() !== '' ? Number(minTradedValueInput) : undefined
      const res = await axios.post(`${API_BASE}/stocks/sync`, {
        mode,
        force: forceSync,
        min_market_cap_cr: minCap !== undefined && !Number.isNaN(minCap) ? minCap : undefined,
        min_traded_value_cr: minTv !== undefined && !Number.isNaN(minTv) ? minTv : undefined,
      })
      return res.data
    },
    onSuccess: (data) => {
      if (data?.job_id) {
        setActiveJobId(data.job_id)
      }
      setFeedbackMsg(
        `Sync complete: ${data?.total_synced ?? 0} stocks synced · ${data?.set2_pass_count ?? 0} pass Set 2 · ${data?.set1_pass_count ?? 0} pass Set 1`
      )
      queryClient.invalidateQueries({ queryKey: ['stocksSyncStatus'] })
      queryClient.invalidateQueries({ queryKey: ['stocksList'] })
      onSynced?.()
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail
      const msg =
        typeof detail === 'string'
          ? detail
          : detail?.message || err?.message || 'Stock sync failed'
      setErrorMsg(msg)
    },
  })

  const statusData = statusQuery.data
  const syncedToday = Boolean(statusData?.synced_today)
  const totalUniverse = statusData?.total_universe ?? 21
  const totalSynced = statusData?.total_synced ?? 0
  const failedCount = statusData?.failed ?? statusData?.last_run?.failed_symbols ?? 0
  const passSet2 = statusData?.set2_pass_count ?? 0
  const passSet1 = statusData?.set1_pass_count ?? 0
  const lastCompletedAt = statusData?.last_run?.completed_at || statusData?.finished

  const progressTotal = jobQuery.data?.total || statusData?.total || totalUniverse || 1
  const progressDone = jobQuery.data?.done || statusData?.done || totalSynced || 0
  const progressPct = Math.min(100, Math.round((progressDone / Math.max(1, progressTotal)) * 100))

  return (
    <div className={`rounded-xl border border-indigo-200 bg-indigo-50/40 ${compact ? 'p-3' : 'p-4'} space-y-3`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-indigo-600" />
          <span className="text-xs font-bold uppercase tracking-wider text-indigo-900">
            NSE Direct Equity Universe Sync
          </span>
          {statusData?.has_synced_data ? (
            <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">
              <CheckCircle2 className="w-3 h-3" />
              {totalUniverse} in universe · {totalSynced} fetched · {failedCount} failed · {passSet2} pass Set 2 ({passSet1} Set 1)
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">
              <AlertTriangle className="w-3 h-3" />
              Not synced yet
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label
            className={`inline-flex items-center gap-1.5 text-xs ${
              syncedToday ? 'text-gray-700 cursor-pointer' : 'text-gray-400 cursor-not-allowed'
            }`}
            title={
              syncedToday
                ? 'Bypass once-per-day guard and re-sync today'
                : 'Force is only needed when a sync has already completed today'
            }
          >
            <input
              type="checkbox"
              checked={forceSync}
              disabled={!syncedToday}
              onChange={(e) => setForceSync(e.target.checked)}
              className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <span>Force today</span>
          </label>

          <button
            type="button"
            onClick={() => syncMutation.mutate('full')}
            disabled={syncMutation.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition shadow-sm"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
            {syncMutation.isPending ? 'Syncing...' : 'Sync Stocks'}
          </button>

          {failedCount > 0 && (
            <button
              type="button"
              onClick={() => syncMutation.mutate('retry_failed')}
              disabled={syncMutation.isPending}
              className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg border border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 disabled:opacity-50"
            >
              <RotateCcw className="w-3 h-3" />
              Retry Failed ({failedCount})
            </button>
          )}

          {totalSynced > 0 && (
            <button
              type="button"
              onClick={() => syncMutation.mutate('rescreen')}
              disabled={syncMutation.isPending}
              className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-lg border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              title="Recompute sector medians and re-run Set 1 & Set 2 screens without network calls"
            >
              <Filter className="w-3 h-3" />
              Re-screen
            </button>
          )}
        </div>
      </div>

      {/* Optional Minimum Market Cap & Traded Value Filters */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
        <div>
          <label className="block text-[11px] font-medium text-gray-600 mb-0.5">
            Optional Min Market Cap (₹ Cr)
          </label>
          <input
            type="number"
            min="0"
            placeholder="Leave empty for no limit"
            value={minMarketCapInput}
            onChange={(e) => setMinMarketCapInput(e.target.value)}
            className="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-900 focus:border-indigo-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-[11px] font-medium text-gray-600 mb-0.5">
            Optional Min Daily Traded Value (₹ Cr)
          </label>
          <input
            type="number"
            min="0"
            placeholder="Leave empty for no limit"
            value={minTradedValueInput}
            onChange={(e) => setMinTradedValueInput(e.target.value)}
            className="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-900 focus:border-indigo-500 focus:outline-none"
          />
        </div>
        <div className="flex flex-col justify-end text-[11px] text-gray-500">
          {lastCompletedAt ? (
            <span>Last synced: {new Date(lastCompletedAt).toLocaleString()}</span>
          ) : (
            <span>No market-cap limit applied unless entered above.</span>
          )}
          {statusData?.last_run?.min_market_cap_cr != null && (
            <span className="text-indigo-700 font-medium">
              Sync filter: ≥ ₹{Number(statusData.last_run.min_market_cap_cr).toLocaleString('en-IN')} Cr
            </span>
          )}
        </div>
      </div>

      {/* Progress Bar when running */}
      {(syncMutation.isPending || jobQuery.data?.status === 'running') && (
        <div className="space-y-1">
          <div className="flex justify-between text-[11px] text-indigo-700 font-medium">
            <span>Syncing & computing sector medians...</span>
            <span>
              {progressDone} / {progressTotal} ({progressPct}%)
            </span>
          </div>
          <div className="w-full h-2 bg-indigo-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-indigo-600 transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {feedbackMsg && (
        <div className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5">
          {feedbackMsg}
        </div>
      )}
      {errorMsg && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">
          {errorMsg}
        </div>
      )}
    </div>
  )
}
