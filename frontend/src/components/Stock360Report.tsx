import { useMemo, useState } from 'react'
import axios from 'axios'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { FileText, Loader2, RefreshCw, X, Star } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'

const API_BASE = 'http://localhost:8000/api'

type MetricStatus =
  | 'verified' | 'computed' | 'estimated' | 'manual'
  | 'unavailable' | 'requires_live_verification' | 'not_applicable'

interface Metric {
  id: string; label: string; value: number | string | null; unit: string; status: MetricStatus
  as_of?: string | null; source: string; url: string; formula: string; inputs: string[]
}
interface SectionTable { title: string; columns: string[]; rows: (string | number | null)[][] }
interface Section {
  id: string; title: string; part: 1 | 2 | 3 | 4; metrics: Metric[]; tables: SectionTable[]
  commentary: string; verdict?: string | null; status: string; missing: string[]
}
interface Report360Data {
  report_id: string; symbol: string; name: string; data_mode: string; generated_at: string; as_of: string
  sections: Section[]
  mechanical: { score: number; stars: number; action: string; sell_signals: number; evaluable_legends: number }
  final: { action: string; stars: number; thesis: string; clamped_reason: string } | null
  sources: { url: string; title: string; publisher: string; published_at?: string | null }[]
  llm_calls: number; web_searches: number; warnings: string[]; disclosures: string[]
  charts?: Charts
}
interface Charts {
  price?: { dates: string[]; close: number[]; ema20: (number | null)[]; ema50: (number | null)[]; ema200: (number | null)[]; levels: Record<string, number>; cmp: number | null }
  pe_band?: { points: { period: string; pe: number }[]; median: number | null; low: number; high: number; current: number | null }
  decomposition?: { from_eps_pct?: number; from_rerating_pct?: number }
  consensus?: { buy_count?: number; hold_count?: number; sell_count?: number; target_price_mean?: number; cmp?: number | null }
}
type ManualKind = 'num' | 'text' | 'history'
interface HistoryRow { quarter: string; promoter_pct: string; fii_pct: string; dii_pct: string; public_pct: string; pledge_pct: string }
interface ReportResponse { meta: { report_id: string; status: string; created_at: string; error?: string }; report: Report360Data | null }
interface Job { job_id: string; status: string; progress: number; message: string; error?: string }

const STATUS_LABEL: Record<MetricStatus, string> = {
  verified: 'Verified', computed: 'Computed', estimated: 'Estimated', manual: 'Manual',
  unavailable: 'Not available', requires_live_verification: 'Requires live verification', not_applicable: 'N/A',
}
const STATUS_CLASS: Record<MetricStatus, string> = {
  verified: 'bg-emerald-100 text-emerald-800', computed: 'bg-sky-100 text-sky-800',
  estimated: 'bg-amber-100 text-amber-800', manual: 'bg-violet-100 text-violet-800',
  unavailable: 'bg-slate-100 text-slate-500', requires_live_verification: 'bg-orange-100 text-orange-800',
  not_applicable: 'bg-slate-100 text-slate-400',
}
const PART_TITLE: Record<number, string> = {
  1: 'Part 1 — Business & Financials', 2: 'Part 2 — Valuation & Technicals',
  3: 'Part 3 — Legends & Targets', 4: 'Part 4 — Risks, Tax & Final Verdict',
}
const MANUAL_FIELDS: [string, string, ManualKind][] = [
  ['promoter_pct', 'Promoter %', 'num'], ['pledge_pct', 'Pledge %', 'num'], ['fii_pct', 'FII %', 'num'], ['dii_pct', 'DII %', 'num'],
  ['public_pct', 'Public %', 'num'], ['order_book_cr', 'Order book ₹ Cr', 'num'],
  ['contingent_liabilities_cr', 'Contingent liabilities ₹ Cr', 'num'], ['goodwill_cr', 'Goodwill ₹ Cr', 'num'],
  ['consensus_eps_fy1', 'Consensus EPS FY+1 ₹', 'num'], ['consensus_eps_fy2', 'Consensus EPS FY+2 ₹', 'num'],
  ['buy_count', 'Analysts: Buy', 'num'], ['hold_count', 'Analysts: Hold', 'num'], ['sell_count', 'Analysts: Sell', 'num'],
  ['target_price_mean', 'Mean analyst target ₹', 'num'],
  ['industry_cagr_pct', 'Industry CAGR %', 'num'], ['tam_cr', 'TAM ₹ Cr', 'num'],
  ['guidance', 'Management guidance', 'text'],
]
const HISTORY_COLS: (keyof HistoryRow)[] = ['promoter_pct', 'fii_pct', 'dii_pct', 'public_pct', 'pledge_pct']
const DISCLOSURE =
  'Educational analysis only — not investment advice. Items marked Not available / Requires live verification were not fetched and are never estimated. Famous-investor views are simulated applications of published frameworks.'

function Chip({ status }: { status: MetricStatus }) {
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_CLASS[status]}`}>{STATUS_LABEL[status]}</span>
}

function fmt(v: Metric['value']) {
  if (v === null || v === undefined) return '—'
  return typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : v
}

function MetricRow({ m }: { m: Metric }) {
  const tip = [m.formula && `Formula: ${m.formula}`, m.inputs?.length ? `Inputs: ${m.inputs.join(', ')}` : '', m.source && `Source: ${m.source}`, m.as_of && `As of ${m.as_of}`]
    .filter(Boolean).join('\n')
  return (
    <tr className="border-b border-slate-100" title={tip}>
      <td className="py-1 pr-2 text-slate-600">{m.label}</td>
      <td className="py-1 pr-2 text-right font-medium">{m.status === 'unavailable' || m.status === 'requires_live_verification' ? '—' : `${fmt(m.value)} ${m.unit}`}</td>
      <td className="py-1 pr-2"><Chip status={m.status} /></td>
      <td className="py-1 text-xs text-slate-400">
        {m.url ? <a className="underline" href={m.url} target="_blank" rel="noreferrer">{m.source || 'source'}</a> : m.source}
      </td>
    </tr>
  )
}

function numeric(v: unknown): number | null {
  const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/[^0-9.-]/g, ''))
  return Number.isFinite(n) ? n : null
}

function TableView({ t }: { t: SectionTable }) {
  const chartable = t.title.startsWith('Intrinsic value') || t.title.startsWith('Long-term targets')
  const data = useMemo(() => {
    if (!chartable) return []
    if (t.title.startsWith('Intrinsic')) return t.rows.map(r => ({ name: String(r[0]), value: numeric(r[1]) })).filter(d => d.value !== null)
    return t.rows.flatMap(r => [
      { name: `${r[0]}y bear`, value: numeric(r[1]) }, { name: `${r[0]}y base`, value: numeric(r[2]) }, { name: `${r[0]}y bull`, value: numeric(r[3]) },
    ]).filter(d => d.value !== null)
  }, [t, chartable])
  return (
    <div className="my-3">
      <div className="mb-1 text-xs font-semibold text-slate-500">{t.title}</div>
      {data.length > 1 && (
        <div className="mb-2 h-48 w-full">
          <ResponsiveContainer>
            <BarChart data={data}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} /><Tooltip /><Bar dataKey="value" fill="#6366f1" /></BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr>{t.columns.map(c => <th key={c} className="border-b px-2 py-1 text-left font-semibold text-slate-500">{c}</th>)}</tr></thead>
          <tbody>{t.rows.map((r, i) => <tr key={i} className="border-b border-slate-100">{r.map((c, j) => <td key={j} className="px-2 py-1">{c === null ? 'Not available' : String(c)}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  )
}

function SectionView({ s }: { s: Section }) {
  return (
    <section id={`s360-${s.id}`} className="mb-6 scroll-mt-4">
      <h4 className="flex items-center gap-2 text-sm font-bold text-slate-800">
        {s.title}
        {s.verdict && <span className="rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-700">{s.verdict}</span>}
        {s.status !== 'complete' && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{s.status}</span>}
      </h4>
      {s.commentary && <p className="my-2 whitespace-pre-line text-sm text-slate-700">{s.commentary}</p>}
      {s.metrics.length > 0 && (
        <table className="my-2 w-full text-xs"><tbody>{s.metrics.map(m => <MetricRow key={m.id} m={m} />)}</tbody></table>
      )}
      {s.tables.map((t, i) => <TableView key={i} t={t} />)}
      {s.missing.length > 0 && <div className="text-[11px] text-slate-400">Not available: {s.missing.join('; ')}</div>}
    </section>
  )
}

function Stock360Charts({ c }: { c?: Charts }) {
  const price = useMemo(
    () => (c?.price ? c.price.dates.map((d, i) => ({ d, close: c.price!.close[i], ema20: c.price!.ema20[i], ema50: c.price!.ema50[i], ema200: c.price!.ema200[i] })) : []),
    [c],
  )
  if (!c || Object.keys(c).length === 0) return null
  const cons = c.consensus
  const consData = cons ? [{ name: 'Buy', v: cons.buy_count }, { name: 'Hold', v: cons.hold_count }, { name: 'Sell', v: cons.sell_count }].filter(x => x.v !== undefined) : []
  const dec = c.decomposition
  const decData = dec ? [{ name: 'EPS growth', v: dec.from_eps_pct }, { name: 'P/E re-rating', v: dec.from_rerating_pct }].filter(x => x.v !== undefined) : []
  const box = 'rounded border border-slate-200 p-2'
  const cap = 'mb-1 text-xs font-semibold text-slate-500'
  return (
    <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2" data-testid="s360-charts">
      {c.price && price.length > 1 && (
        <div className={`${box} md:col-span-2`}>
          <div className={cap}>Price with EMA 20/50/200 and support / resistance (last {price.length} sessions)</div>
          <div className="h-64 w-full">
            <ResponsiveContainer>
              <LineChart data={price}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} /><YAxis domain={['auto', 'auto']} tick={{ fontSize: 10 }} /><Tooltip /><Legend />
                <Line dataKey="close" name="Close" stroke="#0f172a" dot={false} strokeWidth={1.5} />
                <Line dataKey="ema20" name="EMA20" stroke="#22c55e" dot={false} />
                <Line dataKey="ema50" name="EMA50" stroke="#f59e0b" dot={false} />
                <Line dataKey="ema200" name="EMA200" stroke="#ef4444" dot={false} />
                {Object.entries(c.price.levels).map(([k, v]) => (
                  <ReferenceLine key={k} y={v} stroke="#94a3b8" strokeDasharray="4 4" label={{ value: k, fontSize: 9, position: 'insideTopLeft' }} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {c.pe_band && (
        <div className={box}>
          <div className={cap}>P/E band (own history; median {c.pe_band.median ?? '—'}, current {c.pe_band.current ?? '—'})</div>
          <div className="h-48 w-full">
            <ResponsiveContainer>
              <LineChart data={c.pe_band.points}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="period" tick={{ fontSize: 9 }} /><YAxis domain={['auto', 'auto']} tick={{ fontSize: 10 }} /><Tooltip />
                <Line dataKey="pe" name="P/E" stroke="#6366f1" />
                {c.pe_band.median !== null && <ReferenceLine y={c.pe_band.median} stroke="#16a34a" strokeDasharray="4 4" label={{ value: 'median', fontSize: 9 }} />}
                <ReferenceLine y={c.pe_band.low} stroke="#94a3b8" strokeDasharray="2 4" label={{ value: 'low', fontSize: 9 }} />
                <ReferenceLine y={c.pe_band.high} stroke="#94a3b8" strokeDasharray="2 4" label={{ value: 'high', fontSize: 9 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {decData.length > 0 && (
        <div className={box}>
          <div className={cap}>Return decomposition since last FY end (percentage points)</div>
          <div className="h-48 w-full">
            <ResponsiveContainer>
              <BarChart data={decData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} /><Tooltip /><ReferenceLine y={0} stroke="#475569" /><Bar dataKey="v" name="pp" fill="#0ea5e9" /></BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
      {cons && (consData.length > 0 || cons.target_price_mean !== undefined) && (
        <div className={box}>
          <div className={cap}>Analyst consensus (manual / web-cited)</div>
          {consData.length > 0 && (
            <div className="h-36 w-full">
              <ResponsiveContainer>
                <BarChart data={consData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" tick={{ fontSize: 10 }} /><YAxis allowDecimals={false} tick={{ fontSize: 10 }} /><Tooltip /><Bar dataKey="v" name="Analysts" fill="#8b5cf6" /></BarChart>
              </ResponsiveContainer>
            </div>
          )}
          {cons.target_price_mean !== undefined && (
            <div className="mt-1 text-xs text-slate-600">
              Mean target ₹{cons.target_price_mean.toLocaleString('en-IN')}
              {cons.cmp ? ` vs CMP ₹${cons.cmp.toLocaleString('en-IN')} (${(((cons.target_price_mean / cons.cmp) - 1) * 100).toFixed(1)}%)` : ''}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

type SavedInputs = Record<string, { value: number | string | Record<string, number | string>[]; as_of: string }>

function ManualInputsFields({ symbol, initial, onSaved }: { symbol: string; initial: SavedInputs; onSaved: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(initial).filter(([k]) => k !== 'shareholding_history').map(([k, v]) => [k, String(v.value)])))
  const [rows, setRows] = useState<HistoryRow[]>(() => {
    const h = initial.shareholding_history?.value
    return Array.isArray(h) ? h.map(r => ({ quarter: String(r.quarter ?? ''), promoter_pct: String(r.promoter_pct ?? ''), fii_pct: String(r.fii_pct ?? ''), dii_pct: String(r.dii_pct ?? ''), public_pct: String(r.public_pct ?? ''), pledge_pct: String(r.pledge_pct ?? '') })) : []
  })
  const [asOf, setAsOf] = useState('')
  const [url, setUrl] = useState('')
  const [err, setErr] = useState('')
  const save = useMutation({
    mutationFn: async () => {
      const inputs: Record<string, unknown> = {}
      for (const [k, , kind] of MANUAL_FIELDS) {
        const v = vals[k]
        if (v === undefined || v === '') continue
        inputs[k] = { value: kind === 'num' ? Number(v) : v, as_of: asOf, source_url: url }
      }
      const hist = rows.filter(r => r.quarter.trim()).map(r => {
        const o: Record<string, string | number> = { quarter: r.quarter.trim() }
        for (const c of HISTORY_COLS) if (r[c] !== '') o[c] = Number(r[c])
        return o
      })
      if (hist.length) inputs.shareholding_history = { value: hist, as_of: asOf, source_url: url }
      await axios.put(`${API_BASE}/stocks/${symbol}/manual-inputs`, { inputs })
    },
    onSuccess: () => { setErr(''); onSaved() },
    onError: (e: unknown) => setErr(axios.isAxiosError(e) ? JSON.stringify(e.response?.data?.detail ?? e.message) : 'Save failed'),
  })
  const setRow = (i: number, k: keyof HistoryRow, v: string) => setRows(rs => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <details className="mb-4 rounded border border-slate-200 p-3 text-xs">
      <summary className="cursor-pointer font-semibold text-slate-600">Manual inputs (shareholding, consensus, guidance, order book…) — shown as “Manual”</summary>
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
        {MANUAL_FIELDS.filter(f => f[2] === 'num').map(([k, label]) => (
          <label key={k} className="flex flex-col gap-0.5">{label}
            <input className="rounded border px-1 py-0.5" type="number" value={vals[k] ?? ''} onChange={e => setVals({ ...vals, [k]: e.target.value })} />
          </label>
        ))}
      </div>
      <label className="mt-2 flex flex-col gap-0.5">Management guidance
        <textarea className="rounded border px-1 py-0.5" rows={2} maxLength={1000} value={vals.guidance ?? ''} onChange={e => setVals({ ...vals, guidance: e.target.value })} />
      </label>
      <div className="mt-3 font-semibold text-slate-600">Shareholding history (by quarter, %)</div>
      <table className="mt-1 w-full">
        <thead><tr><th className="text-left">Quarter</th>{HISTORY_COLS.map(c => <th key={c} className="text-left">{c.replace('_pct', '')}</th>)}<th /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><input className="w-20 rounded border px-1 py-0.5" placeholder="Mar-25" value={r.quarter} onChange={e => setRow(i, 'quarter', e.target.value)} /></td>
              {HISTORY_COLS.map(c => <td key={c}><input className="w-16 rounded border px-1 py-0.5" type="number" value={r[c]} onChange={e => setRow(i, c, e.target.value)} /></td>)}
              <td><button type="button" aria-label="Remove row" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}><X size={12} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="mt-1 rounded border px-2 py-0.5" disabled={rows.length >= 12}
        onClick={() => setRows(rs => [...rs, { quarter: '', promoter_pct: '', fii_pct: '', dii_pct: '', public_pct: '', pledge_pct: '' }])}>+ Add quarter</button>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-0.5">As of (required)
          <input className="rounded border px-1 py-0.5" type="date" value={asOf} onChange={e => setAsOf(e.target.value)} /></label>
        <label className="flex flex-col gap-0.5">Source URL
          <input className="rounded border px-1 py-0.5" value={url} onChange={e => setUrl(e.target.value)} /></label>
      </div>
      {err && <div className="mt-1 text-red-600">{err}</div>}
      <button className="mt-2 rounded bg-indigo-600 px-3 py-1 text-white disabled:opacity-50" disabled={!asOf || save.isPending} onClick={() => save.mutate()}>
        Save &amp; regenerate
      </button>
    </details>
  )
}

function ManualInputsForm({ symbol, onSaved }: { symbol: string; onSaved: () => void }) {
  const q = useQuery({
    queryKey: ['s360-manual', symbol],
    queryFn: async () => (await axios.get(`${API_BASE}/stocks/${symbol}/manual-inputs`)).data as { inputs: SavedInputs },
  })
  if (q.isLoading) return null
  return <ManualInputsFields key={`${symbol}-${q.dataUpdatedAt}`} symbol={symbol} initial={q.data?.inputs ?? {}} onSaved={onSaved} />
}

export function Stock360Panel({ symbol, runId, context, horizonYears, initialReportId, onClose }: {
  symbol: string; runId?: string; context?: 'advise' | 'analyzer' | 'compare' | 'history'; horizonYears?: number
  initialReportId?: string; onClose?: () => void
}) {
  const qc = useQueryClient()
  const jobId = useAppStore(st => st.active360Jobs[symbol] ?? null)
  const setActiveJob = useAppStore(st => st.setActive360Job)
  const [chosenReport, setChosenReport] = useState<string | null>(initialReportId ?? null)
  const [error, setError] = useState('')

  const latest = useQuery({
    queryKey: ['s360-latest', symbol], enabled: !initialReportId,
    queryFn: async () => (await axios.get(`${API_BASE}/stocks/${symbol}/report360/latest`)).data as ReportResponse,
    retry: false,
  })
  const reportId = chosenReport ?? latest.data?.meta.report_id ?? null

  const start = useMutation({
    mutationFn: async (force: boolean) =>
      (await axios.post(`${API_BASE}/stocks/${symbol}/report360`, { force, run_id: runId, context, horizon_years: horizonYears })).data as { report_id: string; job_id: string | null },
    onSuccess: d => { setError(''); setChosenReport(d.report_id); setActiveJob(symbol, d.job_id) },
    onError: (e: unknown) => setError(axios.isAxiosError(e) ? String(e.response?.data?.detail ?? e.message) : 'Failed to start'),
  })

  const job = useQuery({
    queryKey: ['s360-job', jobId], enabled: !!jobId,
    queryFn: async () => (await axios.get(`${API_BASE}/stocks/report360/jobs/${jobId}`)).data as Job,
    refetchInterval: q => (q.state.data && ['completed', 'failed'].includes(q.state.data.status) ? false : 1500),
  })
  const running = !!jobId && job.data?.status !== 'completed' && job.data?.status !== 'failed' && !job.isError
  const report = useQuery({
    queryKey: ['s360-report', reportId, job.data?.status], enabled: !!reportId && !running,
    queryFn: async () => (await axios.get(`${API_BASE}/stocks/report360/${reportId}`)).data as ReportResponse,
  })
  const rep = report.data?.report ?? null
  const parts = useMemo(() => [1, 2, 3, 4].map(p => ({ p, items: (rep?.sections ?? []).filter(s => s.part === p) })), [rep])

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 font-bold"><FileText size={18} /> 360° Report — {rep?.name ?? symbol}</div>
        <div className="flex items-center gap-2">
          <button className="flex items-center gap-1 rounded border px-2 py-1 text-xs" disabled={running || start.isPending} onClick={() => start.mutate(!!rep)}>
            <RefreshCw size={12} /> {rep ? 'Regenerate' : 'Generate'}
          </button>
          {onClose && <button aria-label="Close" onClick={onClose}><X size={18} /></button>}
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <nav className="hidden w-44 shrink-0 overflow-y-auto border-r p-3 text-xs md:block">
          {parts.map(({ p, items }) => items.length > 0 && (
            <div key={p} className="mb-3">
              <div className="mb-1 font-semibold text-slate-500">{PART_TITLE[p].split(' — ')[0]}</div>
              {items.map(s => <a key={s.id} href={`#s360-${s.id}`} className="block truncate py-0.5 text-slate-600 hover:text-indigo-600">{s.title}</a>)}
            </div>
          ))}
        </nav>
        <div className="min-w-0 flex-1 overflow-y-auto p-4">
          {error && <div className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700">{error}</div>}
          {running && (
            <div className="mb-4 rounded border border-indigo-200 bg-indigo-50 p-3 text-sm">
              <div className="flex items-center gap-2"><Loader2 className="animate-spin" size={14} /> {job.data?.message || 'Starting…'}</div>
              <div className="mt-2 h-1.5 w-full rounded bg-indigo-100"><div className="h-1.5 rounded bg-indigo-600" style={{ width: `${job.data?.progress ?? 2}%` }} /></div>
            </div>
          )}
          {job.data?.status === 'failed' && <div className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700">Report failed: {job.data.error}</div>}
          {!rep && !running && !start.isPending && job.data?.status !== 'failed' && (
            <div className="text-sm text-slate-500">No report yet. Generating one fetches price history, quarterly results and web evidence (takes about a minute).
              <button className="ml-2 rounded bg-indigo-600 px-3 py-1 text-white" onClick={() => start.mutate(false)}>Generate 360° report</button></div>
          )}
          {rep && (
            <>
              <div className="mb-4 rounded-lg border bg-slate-50 p-4">
                <div className="flex items-center justify-between">
                  <div className="text-2xl font-extrabold">{rep.final?.action ?? rep.mechanical.action}</div>
                  <div className="flex items-center gap-1 text-amber-500"><Star size={16} fill="currentColor" /> {(rep.final?.stars ?? rep.mechanical.stars).toFixed(1)}/5</div>
                </div>
                <div className="mt-1 text-xs text-slate-500">Quant score {rep.mechanical.score.toFixed(0)}/100 · sell signals {rep.mechanical.sell_signals}/{rep.mechanical.evaluable_legends} · as of {rep.as_of} · generated {new Date(rep.generated_at).toLocaleString()}</div>
                {rep.final?.thesis && <p className="mt-2 text-sm">{rep.final.thesis}</p>}
                {rep.final?.clamped_reason && <p className="mt-1 text-xs text-orange-700">{rep.final.clamped_reason}</p>}
                {report.data?.meta.status === 'partial' && <p className="mt-1 text-xs text-orange-700">Partial report: some sections use mechanical text only.</p>}
              </div>
              {rep.warnings.length > 0 && <ul className="mb-3 list-disc pl-5 text-xs text-amber-700">{rep.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              <Stock360Charts c={rep.charts} />
              <ManualInputsForm symbol={symbol} onSaved={() => { qc.invalidateQueries({ queryKey: ['s360-manual', symbol] }); start.mutate(true) }} />
              {parts.map(({ p, items }) => items.length > 0 && (
                <div key={p}><h3 className="mb-3 mt-6 border-b pb-1 text-base font-extrabold text-indigo-700">{PART_TITLE[p]}</h3>{items.map(s => <SectionView key={s.id} s={s} />)}</div>
              ))}
              {rep.sources.length > 0 && (
                <div className="mt-6 text-xs"><div className="font-semibold">Web sources</div>
                  <ul className="list-disc pl-5">{rep.sources.map(s => <li key={s.url}><a className="text-indigo-600 underline" href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a> {s.publisher} {s.published_at ?? ''}</li>)}</ul></div>
              )}
              <footer className="mt-6 border-t pt-3 text-[11px] text-slate-500">{rep.disclosures.length ? rep.disclosures.join(' ') : DISCLOSURE}</footer>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export function Stock360Drawer({ symbol, runId, context, horizonYears, reportId, onClose }: {
  symbol: string; runId?: string; context?: 'advise' | 'analyzer' | 'compare' | 'history'; horizonYears?: number
  reportId?: string; onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div className="flex h-full w-full max-w-4xl flex-col shadow-xl" onClick={e => e.stopPropagation()}>
        <Stock360Panel key={`${symbol}-${reportId ?? ''}`} symbol={symbol} runId={runId} context={context} horizonYears={horizonYears} initialReportId={reportId} onClose={onClose} />
      </div>
    </div>
  )
}

/** Single app-wide drawer driven by the store (mounted once in App). */
export function Stock360Host() {
  const target = useAppStore(st => st.open360)
  const close = useAppStore(st => st.closeStock360)
  if (!target) return null
  return <Stock360Drawer symbol={target.symbol} runId={target.runId} context={target.context} horizonYears={target.horizonYears} reportId={target.reportId} onClose={close} />
}

/** Reports linked to a saved advise run (from /api/history/{run_id}); reopens them without regenerating. */
export function SavedReports360({ runId, reports }: { runId: string; reports?: { report_id: string; symbol: string; status: string; created_at: string }[] }) {
  const open = useAppStore(st => st.openStock360)
  const usable = (reports ?? []).filter(r => r.status === 'complete' || r.status === 'partial')
  if (usable.length === 0) return null
  return (
    <div className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-3 text-xs" data-testid="saved-reports360">
      <div className="mb-1 font-semibold text-indigo-800">360° reports saved for this run</div>
      <div className="flex flex-wrap gap-2">
        {usable.map(r => (
          <button key={r.report_id} type="button" className="rounded border border-indigo-200 bg-white px-2 py-0.5 text-indigo-700 hover:bg-indigo-50"
            onClick={() => open({ symbol: r.symbol, reportId: r.report_id, runId, context: 'history' })}>
            {r.symbol} · {new Date(r.created_at).toLocaleDateString()}
          </button>
        ))}
      </div>
    </div>
  )
}

export function Stock360Button({ symbol, badge, runId, context, horizonYears }: {
  symbol: string
  badge?: { action: string; stars: number } | null
  runId?: string
  context?: 'advise' | 'analyzer' | 'compare' | 'history'
  horizonYears?: number
}) {
  const open = useAppStore(st => st.openStock360)
  return (
    <button type="button" onClick={() => open({ symbol, runId, context, horizonYears })} className="inline-flex items-center gap-1 rounded border border-indigo-200 px-2 py-0.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50">
      <FileText size={12} /> 360° Report
      {badge && <span className="rounded bg-indigo-100 px-1 text-[10px]">{badge.action} · {badge.stars.toFixed(1)}★</span>}
    </button>
  )
}

export default Stock360Button
