import { CheckCircle2, AlertTriangle } from 'lucide-react'

interface NotesConfirmProps {
  preview: { understood: string[]; warnings: string[]; llm_calls_budget: number }
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
}

export function NotesConfirm({ preview, onConfirm, onCancel, busy = false }: NotesConfirmProps) {
  return (
    <div className="bg-white p-5 rounded-xl shadow-sm border border-indigo-200 space-y-3">
      <div className="flex items-center gap-2 text-sm font-bold text-gray-900">
        <CheckCircle2 className="w-4 h-4 text-indigo-600" />
        Here is what I understood from your notes
      </div>
      <ul className="text-sm text-gray-800 list-disc pl-5 space-y-0.5">
        {preview.understood.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p className="text-xs text-gray-500">
        Only these will be in the plan. Equity/debt is split 60/40 and equity is shared 50/50 between funds
        and stocks. Your notes override horizon and risk-profile limits; each override is listed in the
        result.
      </p>
      {preview.warnings.length > 0 && (
        <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800 space-y-1">
          {preview.warnings.map((w) => (
            <p key={w} className="flex items-start gap-1">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
              {w}
            </p>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
        >
          Confirm &amp; get advice
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          Edit my notes
        </button>
      </div>
    </div>
  )
}

interface SpecReportData {
  requested?: string[]
  delivered?: Array<{ request: string; delivered: number; instruments: string[] }>
  shortfalls?: string[]
  overrides?: string[]
  warnings?: string[]
}

export function SpecReport({ report }: { report?: SpecReportData | null }) {
  if (!report || !report.requested || report.requested.length === 0) return null
  return (
    <div className="bg-white p-5 rounded-xl shadow-sm border border-indigo-100 space-y-3">
      <h3 className="text-sm font-bold text-gray-900">Requested vs delivered (from your notes)</h3>
      <ul className="text-xs text-gray-700 space-y-1">
        {(report.delivered || []).map((d) => (
          <li key={d.request}>
            <span className="font-semibold">{d.request}</span>: {d.delivered} in plan
            {d.instruments.length > 0 && (
              <span className="text-gray-500"> — {d.instruments.join(', ')}</span>
            )}
          </li>
        ))}
      </ul>
      {(report.shortfalls || []).length > 0 && (
        <div className="p-2.5 rounded-lg bg-red-50 border border-red-200 text-xs text-red-800 space-y-1">
          <p className="font-semibold">Fell short</p>
          {report.shortfalls!.map((m) => (
            <p key={m}>• {m}</p>
          ))}
        </div>
      )}
      {(report.overrides || []).length > 0 && (
        <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800 space-y-1">
          <p className="font-semibold">Rules your request overrode</p>
          {report.overrides!.map((m) => (
            <p key={m}>• {m}</p>
          ))}
        </div>
      )}
      {(report.warnings || []).length > 0 && (
        <div className="text-xs text-gray-600 space-y-1">
          {report.warnings!.map((m) => (
            <p key={m}>• {m}</p>
          ))}
        </div>
      )}
    </div>
  )
}
