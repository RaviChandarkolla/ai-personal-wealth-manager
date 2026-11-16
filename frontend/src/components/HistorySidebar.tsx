import { useState } from 'react'
import axios from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  History,
  Trash2,
  Plus,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
  Clock,
} from 'lucide-react'
import { useAppStore } from '../store/useAppStore'

const API_BASE = 'http://localhost:8000/api'

export interface HistorySummary {
  run_id: string
  created_at: string
  title: string
  query: string | null
  goal_type: string | null
  horizon_years: number | null
  risk_preference: string | null
  amount: number | null
  risk_category: string | null
  risk_score: number | null
  rule_set: string
  data_mode: string
  n_recommendations: number
  message_count?: number
  followup_count?: number
}

function formatRelativeTime(isoString: string): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return isoString
  const diffMs = Date.now() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

interface HistorySidebarProps {
  onNewAdvice: () => void
}

export default function HistorySidebar({ onNewAdvice }: HistorySidebarProps) {
  const queryClient = useQueryClient()
  const activeRunId = useAppStore((state) => state.activeRunId)
  const setActiveRunId = useAppStore((state) => state.setActiveRunId)
  const historySidebarOpen = useAppStore((state) => state.historySidebarOpen)
  const toggleHistorySidebar = useAppStore((state) => state.toggleHistorySidebar)

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const historyQuery = useQuery({
    queryKey: ['history'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/history`, { params: { limit: 50 } })
      return res.data as HistorySummary[]
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (runId: string) => {
      await axios.delete(`${API_BASE}/history/${encodeURIComponent(runId)}`)
      return runId
    },
    onSuccess: (deletedId) => {
      setConfirmDeleteId(null)
      if (activeRunId === deletedId) {
        setActiveRunId(null)
      }
      queryClient.invalidateQueries({ queryKey: ['history'] })
      queryClient.removeQueries({ queryKey: ['history', deletedId] })
    },
  })

  const items = historyQuery.data || []

  if (!historySidebarOpen) {
    return (
      <aside className="flex flex-col items-center bg-white border border-gray-200 rounded-xl shadow-xs py-3 px-2 h-fit sticky top-6">
        <button
          type="button"
          onClick={toggleHistorySidebar}
          className="p-2 rounded-lg text-gray-600 hover:bg-gray-100 hover:text-indigo-600 transition"
          title="Expand Advice History"
        >
          <ChevronRight className="w-5 h-5" />
        </button>
        <button
          type="button"
          onClick={onNewAdvice}
          className="mt-2 p-2 rounded-lg bg-indigo-50 text-indigo-600 hover:bg-indigo-100 transition"
          title="New Advice"
        >
          <Plus className="w-5 h-5" />
        </button>
        <div className="mt-3 flex flex-col items-center gap-1 text-[10px] font-semibold text-gray-400">
          <History className="w-4 h-4 text-gray-400" />
          <span>{items.length}</span>
        </div>
      </aside>
    )
  }

  return (
    <aside className="w-full lg:w-72 shrink-0 bg-white border border-gray-200 rounded-xl shadow-xs flex flex-col max-h-[calc(100vh-3rem)] lg:sticky lg:top-6 overflow-hidden">
      {/* Header */}
      <div className="p-3.5 border-b border-gray-100 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <History className="w-4 h-4 text-indigo-600" />
          <h2 className="text-sm font-bold text-gray-800">Advice History</h2>
          <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded-full bg-gray-100 text-gray-600">
            {items.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onNewAdvice}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition cursor-pointer"
            title="Start a fresh advice session"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>New</span>
          </button>
          <button
            type="button"
            onClick={toggleHistorySidebar}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition"
            title="Collapse sidebar"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* History List */}
      <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
        {historyQuery.isLoading ? (
          <div className="p-4 text-xs text-gray-400 text-center">Loading history...</div>
        ) : items.length === 0 ? (
          <div className="p-6 text-center space-y-1.5">
            <Clock className="w-6 h-6 text-gray-300 mx-auto" />
            <p className="text-xs font-medium text-gray-500">No saved advice runs yet</p>
            <p className="text-[11px] text-gray-400">
              Submit a goal to save your portfolio &amp; ask grounded follow-up questions.
            </p>
          </div>
        ) : (
          items.map((item) => {
            const isActive = activeRunId === item.run_id
            const isConfirming = confirmDeleteId === item.run_id
            const followups = item.followup_count ?? Math.floor((item.message_count ?? 0) / 2)

            return (
              <div
                key={item.run_id}
                onClick={() => {
                  if (!isConfirming) {
                    setActiveRunId(item.run_id)
                  }
                }}
                className={`p-3 transition cursor-pointer group relative ${
                  isActive
                    ? 'bg-indigo-50/70 border-l-4 border-l-indigo-600'
                    : 'hover:bg-gray-50'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p
                      className={`text-xs font-semibold truncate ${
                        isActive ? 'text-indigo-950' : 'text-gray-800'
                      }`}
                      title={item.title}
                    >
                      {item.title}
                    </p>
                    {item.query && (
                      <p className="text-[11px] text-gray-500 truncate mt-0.5" title={item.query}>
                        &ldquo;{item.query}&rdquo;
                      </p>
                    )}
                  </div>

                  {!isConfirming ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        setConfirmDeleteId(item.run_id)
                      }}
                      className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 transition"
                      title="Delete from history"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  ) : (
                    <div
                      className="flex items-center gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        disabled={deleteMutation.isPending}
                        onClick={() => deleteMutation.mutate(item.run_id)}
                        className="px-1.5 py-0.5 rounded bg-red-600 text-white text-[10px] font-semibold hover:bg-red-700"
                      >
                        Delete
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteId(null)}
                        className="px-1.5 py-0.5 rounded bg-gray-200 text-gray-700 text-[10px] font-medium hover:bg-gray-300"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </div>

                {/* Badges & Meta */}
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
                  <span
                    className={`px-1.5 py-0.5 rounded font-semibold ${
                      item.data_mode === 'real'
                        ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                        : 'bg-indigo-50 text-indigo-700 border border-indigo-200'
                    }`}
                  >
                    {item.data_mode === 'real' ? 'Real' : 'Synthetic'}
                  </span>
                  <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 font-medium">
                    {item.rule_set === 'set2'
                      ? 'Set 2'
                      : item.rule_set === 'combined'
                      ? 'Combined'
                      : 'Set 1'}
                  </span>
                  <span className="text-gray-400">
                    {item.n_recommendations} {item.n_recommendations === 1 ? 'fund' : 'funds'}
                  </span>
                  {followups > 0 && (
                    <span className="inline-flex items-center gap-0.5 text-indigo-600 font-medium">
                      <MessageSquare className="w-2.5 h-2.5" />
                      {followups}
                    </span>
                  )}
                  <span className="ml-auto text-gray-400">
                    {formatRelativeTime(item.created_at)}
                  </span>
                </div>
              </div>
            )
          })
        )}
      </div>
    </aside>
  )
}
