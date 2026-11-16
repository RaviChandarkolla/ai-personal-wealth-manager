import { useState } from 'react'
import axios from 'axios'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  MessageSquare,
  Send,
  Sparkles,
  ExternalLink,
  ShieldAlert,
  BookOpen,
} from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

export interface ReferencedFundItem {
  code: string
  name?: string
  category?: string
  status?: string
}

export interface SourceItem {
  source_id: string
  doc_type?: string
  title?: string
}

export interface FollowUpMessageItem {
  id: number
  run_id?: string
  created_at: string
  role: 'user' | 'assistant'
  content: string
  referenced_funds: Array<ReferencedFundItem | string>
  sources: Array<SourceItem | string>
}

interface FollowUpChatProps {
  runId: string
  dataMode: string
  recommendations: Array<{
    instrument_id?: string
    name: string
    category?: string
  }>
  initialMessages?: FollowUpMessageItem[]
}

export default function FollowUpChat({
  runId,
  dataMode,
  recommendations,
  initialMessages = [],
}: FollowUpChatProps) {
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState('')
  const [localMessages, setLocalMessages] = useState<FollowUpMessageItem[]>([])
  const [lastSyncedRunId, setLastSyncedRunId] = useState<string>(runId)

  if (lastSyncedRunId !== runId) {
    setLastSyncedRunId(runId)
    setLocalMessages([])
    setQuestion('')
  }

  // Merge server-fetched messages with any optimistic/new messages if not yet refetched
  const displayedMessages: FollowUpMessageItem[] =
    localMessages.length > initialMessages.length ? localMessages : initialMessages

  const followupMutation = useMutation({
    mutationFn: async (q: string) => {
      const res = await axios.post(
        `${API_BASE}/history/${encodeURIComponent(runId)}/followup`,
        { question: q }
      )
      return res.data as {
        message_id: number
        answer: string
        referenced_funds: Array<ReferencedFundItem | string>
        sources: Array<SourceItem | string>
        refused: boolean
      }
    },
    onMutate: async (q: string) => {
      const nowIso = new Date().toISOString()
      const optimisticUser: FollowUpMessageItem = {
        id: Date.now(),
        run_id: runId,
        created_at: nowIso,
        role: 'user',
        content: q,
        referenced_funds: [],
        sources: [],
      }
      setLocalMessages([...displayedMessages, optimisticUser])
    },
    onSuccess: (data, q) => {
      const nowIso = new Date().toISOString()
      const userMsg: FollowUpMessageItem = {
        id: data.message_id - 1,
        run_id: runId,
        created_at: nowIso,
        role: 'user',
        content: q,
        referenced_funds: [],
        sources: [],
      }
      const assistantMsg: FollowUpMessageItem = {
        id: data.message_id,
        run_id: runId,
        created_at: nowIso,
        role: 'assistant',
        content: data.answer,
        referenced_funds: data.referenced_funds || [],
        sources: data.sources || [],
      }
      setLocalMessages([...initialMessages, userMsg, assistantMsg])
      setQuestion('')
      queryClient.invalidateQueries({ queryKey: ['history', runId] })
      queryClient.invalidateQueries({ queryKey: ['history'] })
    },
  })

  const topFundName = recommendations?.[0]?.name || 'the top recommended fund'
  const starterPrompts = [
    `Why was ${topFundName} chosen?`,
    `Why wasn't Parag Parikh Flexi Cap in the list?`,
    `Explain the debt vs equity split for this goal`,
    `Why were Small Cap funds excluded or limited?`,
  ]

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || followupMutation.isPending) return
    followupMutation.mutate(trimmed)
  }

  const handleStarterClick = (promptText: string) => {
    if (followupMutation.isPending) return
    setQuestion(promptText)
    followupMutation.mutate(promptText)
  }

  const errorMsg = followupMutation.error
    ? (followupMutation.error as any).response?.data?.detail ||
      followupMutation.error.message ||
      'Could not process follow-up question'
    : null

  return (
    <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-indigo-50 text-indigo-600">
            <MessageSquare className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-gray-900">
              Ask a Follow-Up Question About This Advice
            </h3>
            <p className="text-xs text-gray-500">
              Grounded strictly in this run&apos;s suitability rules, candidate scores &amp; fund
              catalog — ask why a specific fund was or wasn&apos;t selected.
            </p>
          </div>
        </div>
        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200">
          <Sparkles className="w-3 h-3" />
          <span>Deterministic Exclusion Trace + Grounded QA</span>
        </span>
      </div>

      {/* Starter Chips */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-gray-400">Try asking:</span>
        {starterPrompts.map((prompt, idx) => (
          <button
            key={idx}
            type="button"
            disabled={followupMutation.isPending}
            onClick={() => handleStarterClick(prompt)}
            className="text-xs px-3 py-1.5 rounded-full bg-gray-50 hover:bg-indigo-50 text-gray-700 hover:text-indigo-700 border border-gray-200 hover:border-indigo-200 transition disabled:opacity-50 cursor-pointer"
          >
            {prompt}
          </button>
        ))}
      </div>

      {/* Message Thread */}
      {displayedMessages.length > 0 && (
        <div className="space-y-3 max-h-96 overflow-y-auto pr-1 pt-1">
          {displayedMessages.map((msg) => {
            const isUser = msg.role === 'user'
            return (
              <div
                key={msg.id}
                className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-3xl rounded-xl px-4 py-3 text-sm space-y-2 ${
                    isUser
                      ? 'bg-indigo-600 text-white'
                      : 'bg-gray-50 border border-gray-200 text-gray-800'
                  }`}
                >
                  <p className="whitespace-pre-line leading-relaxed">{msg.content}</p>

                  {!isUser &&
                    ((msg.referenced_funds && msg.referenced_funds.length > 0) ||
                      (msg.sources && msg.sources.length > 0)) && (
                      <div className="pt-2 border-t border-gray-200/80 flex flex-wrap items-center gap-2 text-xs">
                        {msg.referenced_funds &&
                          msg.referenced_funds.map((rf, idx) => {
                            const code = typeof rf === 'string' ? rf : rf.code
                            const label =
                              typeof rf === 'string'
                                ? `Fund #${rf}`
                                : rf.name && rf.name !== rf.code
                                ? `${rf.name} (${rf.code})`
                                : rf.code
                            const status = typeof rf === 'string' ? undefined : rf.status
                            if (status === 'not_found') {
                              return (
                                <span
                                  key={`${code}-${idx}`}
                                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-200 text-[11px]"
                                >
                                  <span>{label} (not in DB)</span>
                                </span>
                              )
                            }
                            return (
                              <a
                                key={`${code}-${idx}`}
                                href={`/?fund=${encodeURIComponent(code)}&run_id=${encodeURIComponent(
                                  runId
                                )}&data_mode=${encodeURIComponent(dataMode)}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-700 border border-indigo-200 font-mono text-[11px] hover:bg-indigo-100 transition"
                                title="Open fund dossier in new tab"
                              >
                                <span>{label}</span>
                                <ExternalLink className="w-3 h-3" />
                              </a>
                            )
                          })}

                        {msg.sources &&
                          msg.sources.map((src, i) => {
                            const label =
                              typeof src === 'string'
                                ? src
                                : src.title || src.source_id
                            return (
                              <span
                                key={i}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white border border-gray-200 text-gray-500 font-mono text-[10px]"
                              >
                                <BookOpen className="w-2.5 h-2.5 text-gray-400" />
                                <span>{label}</span>
                              </span>
                            )
                          })}
                      </div>
                    )}
                </div>
              </div>
            )
          })}
          {followupMutation.isPending && (
            <div className="flex justify-start">
              <div className="bg-gray-50 border border-gray-200 text-gray-500 rounded-xl px-4 py-2.5 text-xs italic">
                Inspecting pipeline trace &amp; fund catalog...
              </div>
            </div>
          )}
        </div>
      )}

      {errorMsg && (
        <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Input Form */}
      <form onSubmit={handleSubmit} className="flex gap-2 pt-1">
        <input
          type="text"
          maxLength={1000}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={followupMutation.isPending}
          placeholder="Ask about any fund, suitability filter, score comparison, or bucket weight..."
          className="flex-1 px-4 py-2.5 text-sm rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={followupMutation.isPending || !question.trim()}
          className="bg-indigo-600 text-white px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-indigo-700 transition disabled:opacity-50 inline-flex items-center gap-2 cursor-pointer"
        >
          <Send className="w-4 h-4" />
          <span>{followupMutation.isPending ? 'Asking...' : 'Ask'}</span>
        </button>
      </form>
    </section>
  )
}
