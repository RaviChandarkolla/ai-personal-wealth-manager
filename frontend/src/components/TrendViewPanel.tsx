import axios from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Newspaper, RefreshCw, AlertTriangle } from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

interface TrendStatus {
  available: boolean
  stale: boolean
  as_of: string | null
  age_days: number | null
  stale_after_days?: number
  sector_count: number
  stock_count: number
}

export default function TrendViewPanel({ disabled = false }: { disabled?: boolean }) {
  const queryClient = useQueryClient()

  const viewQuery = useQuery({
    queryKey: ['trendView'],
    queryFn: async () => (await axios.get(`${API_BASE}/trend-view`)).data as { status: TrendStatus },
  })

  const refreshMutation = useMutation({
    mutationFn: async () => (await axios.post(`${API_BASE}/trend-view/refresh`)).data,
    onSuccess: (data) => queryClient.setQueryData(['trendView'], data),
  })

  const status = viewQuery.data?.status
  const error = refreshMutation.error
    ? (refreshMutation.error as any).response?.data?.detail || (refreshMutation.error as Error).message
    : null

  return (
    <div className="pt-3 border-t border-amber-100 space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2 text-xs text-gray-800">
          <Newspaper className="w-4 h-4 mt-0.5 text-indigo-600 shrink-0" />
          <span>
            <span className="font-bold">Web trend view</span>{' '}
            <span className="text-gray-600">
              (used when your notes ask for trendy stocks; sector and news based, not price based)
            </span>
            <span className="block text-[11px] text-gray-500">
              {status?.available
                ? `Saved ${status.as_of} · ${status.sector_count} sectors · ${status.stock_count} stocks`
                : 'No view saved yet'}
            </span>
          </span>
        </div>
        <button
          type="button"
          onClick={() => refreshMutation.mutate()}
          disabled={disabled || refreshMutation.isPending}
          className="px-3 py-1.5 rounded-lg border border-indigo-200 bg-white text-indigo-700 text-xs font-medium hover:bg-indigo-50 disabled:opacity-50 flex items-center gap-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
          {refreshMutation.isPending ? 'Reading the news…' : 'Refresh trend view'}
        </button>
      </div>
      {status?.available && status.stale && (
        <p className="text-[11px] text-amber-700 flex items-center gap-1">
          <AlertTriangle className="w-3 h-3" />
          This view is {status.age_days} days old (limit {status.stale_after_days ?? 30}). Refresh it for
          current picks.
        </p>
      )}
      {error && <p className="text-[11px] text-red-600">{error}</p>}
    </div>
  )
}
