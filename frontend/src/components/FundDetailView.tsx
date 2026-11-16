import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  Cell,
  ReferenceLine,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from 'recharts'
import {
  TrendingUp,
  ShieldCheck,
  Award,
  FileText,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  RefreshCw,
  Database,
  ArrowLeft,
  Calendar,
  User,
  Building2,
  BarChart3,
  Scale,
  Newspaper,
  ExternalLink,
  Clock,
} from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

interface FundDetailProps {
  code: string
  runId?: string | null
  dataMode?: string | null
  embedded?: boolean
  headerActions?: ReactNode
}

function stripCitations(text: string): { cleanText: string; citations: string[] } {
  const citations: string[] = []
  const cleanText = text
    .replace(/\[([^\[\]]+)\]/g, (_, inner: string) => {
      inner.split(',').forEach((id) => {
        const trimmed = id.trim()
        if (trimmed) citations.push(trimmed)
      })
      return ''
    })
    .replace(/\s+\./g, '.')
    .replace(/\.{2,}/g, '.')
    .trim()
  return { cleanText, citations }
}

const SUBSCORE_META: Record<string, { label: string; defaultWeight: number; desc: string }> = {
  fundamental: {
    label: 'Fundamental Performance',
    defaultWeight: 0.35,
    desc: 'Rolling 3Y return percentile, 5Y CAGR percentile, Sharpe ratio & drawdown',
  },
  fit: {
    label: 'Investor & Horizon Fit',
    defaultWeight: 0.25,
    desc: 'Volatility vs. risk tolerance cap and category minimum horizon fit',
  },
  news: {
    label: 'Macro & News Context',
    defaultWeight: 0.2,
    desc: 'Materiality-weighted regulatory, AMC & category news events (0–100)',
  },
  sentiment: {
    label: 'Market Sentiment',
    defaultWeight: 0.1,
    desc: 'Real-time news & category flow sentiment signal (0–100)',
  },
  liquidity_cost: {
    label: 'Liquidity & Cost Efficiency',
    defaultWeight: 0.1,
    desc: 'Expense ratio vs. category ceiling, AUM scale & direct plan structure',
  },
}

export default function FundDetailView({
  code,
  runId,
  dataMode,
  embedded = false,
  headerActions,
}: FundDetailProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['fundDetail', code, runId, dataMode],
    queryFn: async () => {
      const params: Record<string, string> = {}
      if (runId) params.run_id = runId
      if (dataMode) params.data_mode = dataMode
      const res = await axios.get(`${API_BASE}/funds/${encodeURIComponent(code)}`, { params })
      return res.data
    },
  })

  if (isLoading) {
    return (
      <div className={embedded ? 'py-12 flex items-center justify-center' : 'min-h-screen bg-gray-50 flex items-center justify-center p-8'}>
        <div className="bg-white p-8 rounded-xl shadow-sm border border-gray-100 text-center space-y-3">
          <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-gray-600 font-medium">Loading research dossier for {code}...</p>
        </div>
      </div>
    )
  }

  if (error || !data) {
    const errMsg =
      (error as any)?.response?.data?.detail ||
      (error as any)?.message ||
      `Could not load details for fund ${code}`
    return (
      <div className={embedded ? 'py-4' : 'min-h-screen bg-gray-50 p-8'}>
        <div className="max-w-4xl mx-auto bg-white p-8 rounded-xl shadow-sm border border-red-200">
          <div className="flex items-center gap-3 text-red-600 mb-4">
            <AlertTriangle className="w-6 h-6" />
            <h2 className="text-xl font-bold">Unable to Load Fund Dossier</h2>
          </div>
          <p className="text-gray-700 mb-6">{errMsg}</p>
          {!embedded && (
            <a
              href="/"
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Portfolio Advisor
            </a>
          )}
        </div>
      </div>
    )
  }

  const {
    fund,
    stats,
    nav_history: navHistory,
    documents,
    facts,
    analysis,
    news,
    run_context: runCtx,
    profile,
    checklist,
    last_refresh: lastRefresh,
  } = data
  const navStart = navHistory?.length ? navHistory[0] : null
  const navEnd = navHistory?.length ? navHistory[navHistory.length - 1] : null
  const totalNavReturn =
    navStart && navEnd && navStart.nav > 0
      ? (((navEnd.nav - navStart.nav) / navStart.nav) * 100).toFixed(1)
      : null

  const decisionRationale = runCtx?.rationale ? stripCitations(runCtx.rationale) : null

  const calendarReturnsData = profile?.calendar_returns
    ? Object.entries(profile.calendar_returns)
        .map(([yr, ret]) => ({ year: String(yr), returnPct: Number(ret) }))
        .sort((a, b) => Number(a.year) - Number(b.year))
    : []

  const alphaVal = profile?.alpha ?? stats?.alpha
  const betaVal = profile?.beta ?? stats?.beta
  const cagr5yVal = stats?.cagr_5y ?? profile?.cagr_5y
  const cagr3yVal = stats?.cagr_3y ?? profile?.cagr_3y
  const roll3yVal = stats?.roll_3y_median ?? profile?.roll_3y_median
  const volVal = stats?.volatility ?? profile?.volatility
  const mddVal = stats?.max_drawdown ?? profile?.max_drawdown
  const sharpeVal = stats?.sharpe ?? profile?.sharpe
  const sortinoVal = stats?.sortino ?? profile?.sortino

  const content = (
    <div className={embedded ? 'space-y-6' : 'max-w-6xl mx-auto space-y-6'}>
      {/* Top Navigation / Metadata Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {!embedded ? (
          <a
            href="/"
            className="inline-flex items-center gap-2 text-sm text-gray-600 hover:text-indigo-600 font-medium transition"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Wealth Advisor Dashboard
          </a>
        ) : (
          <div className="flex items-center gap-2">{headerActions}</div>
        )}
        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
          <Database className="w-3.5 h-3.5 text-indigo-500" />
          <span>Source: {fund.source}</span>
          <span>•</span>
          <span>As of: {fund.as_of}</span>
          {lastRefresh?.refreshed_at && (
            <>
              <span>•</span>
              <span
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium"
                title={`Sources: ${(lastRefresh.sources || []).join(', ') || 'mfapi'}`}
              >
                <Clock className="w-3 h-3" />
                Last refreshed: {String(lastRefresh.refreshed_at).replace('T', ' ').slice(0, 19)} UTC
              </span>
            </>
          )}
          {runCtx?.run_id && (
            <>
              <span>•</span>
              <span className="font-mono bg-gray-200 text-gray-700 px-2 py-0.5 rounded">
                Run {runCtx.run_id}
              </span>
            </>
          )}
          {runCtx?.provenance?.model && (
            <>
              <span>•</span>
              <span className="font-mono bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded border border-indigo-100">
                Model: {runCtx.provenance.model}
              </span>
            </>
          )}
        </div>
      </div>

        {/* Fund Header Card */}
        <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 uppercase">
                  {runCtx?.action || 'BUY'}
                </span>
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-50 text-indigo-700 capitalize">
                  {fund.bucket}
                </span>
                <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">
                  {fund.category}
                </span>
                <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 capitalize">
                  {fund.plan} Plan • {fund.option}
                </span>
              </div>
              <h1 className="text-2xl md:text-3xl font-bold text-gray-900">{fund.name}</h1>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-gray-500 pt-1">
                <span className="flex items-center gap-1.5">
                  <span className="font-semibold text-gray-700">Scheme Code:</span>
                  <code className="bg-gray-100 px-1.5 py-0.5 rounded text-gray-800">{fund.code}</code>
                </span>
                {fund.isin && (
                  <span className="flex items-center gap-1.5">
                    <span className="font-semibold text-gray-700">ISIN:</span>
                    <code className="bg-gray-100 px-1.5 py-0.5 rounded text-gray-800">{fund.isin}</code>
                  </span>
                )}
                {fund.amc && (
                  <span className="flex items-center gap-1">
                    <Building2 className="w-3.5 h-3.5 text-gray-400" />
                    {fund.amc}
                  </span>
                )}
                {fund.manager && (
                  <span className="flex items-center gap-1">
                    <User className="w-3.5 h-3.5 text-gray-400" />
                    {fund.manager}
                  </span>
                )}
                {fund.benchmark && (
                  <span className="flex items-center gap-1">
                    <Scale className="w-3.5 h-3.5 text-gray-400" />
                    Benchmark: {fund.benchmark}
                  </span>
                )}
                {fund.launch_date && (
                  <span className="flex items-center gap-1">
                    <Calendar className="w-3.5 h-3.5 text-gray-400" />
                    Launched: {fund.launch_date} ({fund.track_years} yrs)
                  </span>
                )}
              </div>
            </div>

            {/* Analyst Score Badge */}
            <div className="flex items-center gap-4 bg-indigo-50/70 border border-indigo-100 px-5 py-3.5 rounded-xl self-start">
              <div>
                <p className="text-xs font-medium text-indigo-600 uppercase tracking-wider">
                  Analyst Score
                </p>
                <div className="flex items-baseline gap-1 mt-0.5">
                  <span className="text-3xl font-extrabold text-indigo-900">
                    {analysis.score?.toFixed(1)}
                  </span>
                  <span className="text-sm text-indigo-600 font-medium">/ 100</span>
                </div>
                <p className="text-xs text-indigo-700 mt-0.5">
                  Confidence: {((analysis.confidence ?? 1) * 100).toFixed(0)}%
                </p>
              </div>
            </div>
          </div>

          {/* Allocation Banner if opened from a recommendation run */}
          {runCtx && (
            <div className="mt-5 pt-5 border-t border-gray-100 space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 bg-gray-50/80 p-4 rounded-lg">
                <div>
                  <p className="text-xs text-gray-500">Recommended Weight</p>
                  <p className="text-lg font-bold text-gray-900">{runCtx.weight?.toFixed(1)}%</p>
                  {runCtx.bucket_sizing?.bucket_weight != null && (
                    <p className="text-[11px] text-gray-500 capitalize">
                      Out of {runCtx.bucket_sizing.bucket_weight}% {runCtx.bucket_sizing.bucket} bucket
                    </p>
                  )}
                </div>
                <div>
                  <p className="text-xs text-gray-500">Allocated Amount (₹500 step)</p>
                  <p className="text-lg font-bold text-indigo-700">
                    {runCtx.amount != null ? `₹${runCtx.amount.toLocaleString()}` : '-'}
                  </p>
                  {runCtx.raw_amount != null &&
                    runCtx.amount != null &&
                    Math.abs(runCtx.raw_amount - runCtx.amount) >= 1 && (
                      <p className="text-[11px] text-gray-500">
                        Exact weight value: ₹{runCtx.raw_amount.toLocaleString()}
                      </p>
                    )}
                </div>
                <div>
                  <p className="text-xs text-gray-500">Investor Goal</p>
                  <p className="text-sm font-semibold text-gray-800">
                    {runCtx.goal?.amount ? `₹${Number(runCtx.goal.amount).toLocaleString()}` : '-'} •{' '}
                    {runCtx.goal?.horizon_years ?? '-'} Years
                  </p>
                  <p className="text-[11px] text-gray-500 capitalize">
                    Pref: {(runCtx.goal?.risk_preference || 'stated').replace(/_/g, ' ')}
                    {runCtx.goal?.purpose ? ` • Purpose: ${runCtx.goal.purpose}` : ''}
                  </p>
                  {runCtx.goal?.excluded_categories?.length > 0 && (
                    <p className="text-[11px] text-amber-700">
                      Excluded: {runCtx.goal.excluded_categories.join(', ')}
                    </p>
                  )}
                </div>
                <div>
                  <p className="text-xs text-gray-500">Risk Profile</p>
                  <p className="text-sm font-semibold text-gray-800 capitalize">
                    {runCtx.risk?.category || '-'} (Score {runCtx.risk?.score?.toFixed(2) ?? '-'}/10)
                  </p>
                  {runCtx.assumptions && (
                    <p className="text-[11px] text-gray-500">
                      Savings {((runCtx.assumptions.savings_rate ?? 0.2) * 100).toFixed(0)}% •{' '}
                      {runCtx.assumptions.emergency_months ?? 6}m buffer •{' '}
                      {runCtx.assumptions.income_stability ?? 'stable'}
                    </p>
                  )}
                </div>
              </div>

              {/* Decision Rationale, Researcher Note & Bucket Sizing Context */}
              {(decisionRationale || runCtx.research_note || runCtx.bucket_sizing) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                  {decisionRationale && (
                    <div className="bg-indigo-50/40 border border-indigo-100 rounded-lg p-3.5 space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-indigo-900 uppercase tracking-wider text-[11px]">
                          Decision Maker Synthesis Rationale
                        </span>
                        {runCtx.provenance?.decision_prompt && (
                          <span className="font-mono text-[10px] text-indigo-600 bg-indigo-100/70 px-1.5 py-0.5 rounded">
                            {runCtx.provenance.decision_prompt}
                          </span>
                        )}
                      </div>
                      <p className="text-gray-800 leading-relaxed">{decisionRationale.cleanText}</p>
                      {decisionRationale.citations.length > 0 && (
                        <div className="flex flex-wrap gap-1 pt-1">
                          {decisionRationale.citations.map((cid, idx) => (
                            <a
                              key={idx}
                              href={`#fact-${cid}`}
                              className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-800 hover:bg-indigo-200"
                            >
                              {cid}
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {(runCtx.research_note || runCtx.bucket_sizing) && (
                    <div className="bg-gray-50 border border-gray-200 rounded-lg p-3.5 space-y-2">
                      {runCtx.research_note && (
                        <div className="space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="font-semibold text-gray-800 uppercase tracking-wider text-[11px]">
                              MF Researcher Agent Note
                            </span>
                            <span className="font-mono text-[10px] text-gray-500">
                              {runCtx.provenance?.research_prompt || 'mf-research'}
                              {runCtx.research_tool_calls
                                ? ` • ${runCtx.research_tool_calls} tool calls`
                                : ''}
                            </span>
                          </div>
                          <p className="text-gray-700 leading-relaxed">{runCtx.research_note}</p>
                        </div>
                      )}

                      {runCtx.bucket_sizing?.positions?.length > 0 && (
                        <div className="pt-1 border-t border-gray-200/80 space-y-1">
                          <span className="font-semibold text-gray-700 uppercase tracking-wider text-[10px]">
                            {runCtx.bucket_sizing.bucket} Bucket Peer Sizing (Max{' '}
                            {runCtx.sizing_settings?.max_weight_per_instrument ?? 30}% cap • ₹
                            {runCtx.sizing_settings?.min_ticket ?? 500} step):
                          </span>
                          <div className="flex flex-wrap gap-2 pt-0.5">
                            {runCtx.bucket_sizing.positions.map((pos: any) => {
                              const cand = runCtx.bucket_sizing.candidates?.find(
                                (c: any) => c.id === pos.id
                              )
                              const isCurrent = pos.id === code
                              return (
                                <a
                                  key={pos.id}
                                  href={`/fund/${encodeURIComponent(pos.id)}?run_id=${encodeURIComponent(runCtx.run_id)}`}
                                  className={`px-2 py-1 rounded border text-[11px] font-mono transition ${
                                    isCurrent
                                      ? 'bg-indigo-100 border-indigo-300 text-indigo-900 font-semibold'
                                      : 'bg-white border-gray-200 text-gray-700 hover:border-indigo-300'
                                  }`}
                                >
                                  {pos.id}: {pos.weight?.toFixed(1)}% (₹
                                  {pos.amount?.toLocaleString()})
                                  {cand?.score != null ? ` • Score ${cand.score.toFixed(1)}` : ''}
                                </a>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        {/* Conflict Warnings Banner (if any conflicts detected) */}
        {analysis.conflicts && analysis.conflicts.length > 0 && (
          <section className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div className="space-y-1 text-xs text-red-800">
              <p className="font-semibold text-sm">Data / Suitability Conflicts Detected</p>
              <ul className="list-disc pl-4 space-y-1">
                {analysis.conflicts.map((c: any, idx: number) => (
                  <li key={idx}>
                    {c.description}{' '}
                    {c.fields?.length > 0 && (
                      <span className="font-mono text-[11px] text-red-700">
                        ({c.fields.join(', ')})
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}

        {/* Key Metrics Grid */}
        <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Expense Ratio</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {fund.expense_ratio != null ? `${fund.expense_ratio.toFixed(2)}%` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-400 mt-1">
              Category ceiling: ≤ {fund.expense_ceiling?.toFixed(2)}%
            </p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Fund Size (AUM)</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {fund.aum_cr != null ? `₹${fund.aum_cr.toLocaleString()} Cr` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-400 mt-1">Min required: ₹500 Cr</p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Star Rating</p>
            <p className="text-xl font-bold text-amber-600 mt-1 flex items-center gap-1">
              {fund.rating != null ? (
                <>
                  <span>{fund.rating.toFixed(1)}</span>
                  <span>★</span>
                </>
              ) : (
                <span className="text-sm font-semibold text-gray-500">Unrated (Passive)</span>
              )}
            </p>
            <p className="text-[11px] text-gray-400 mt-1">
              {fund.rating != null ? 'Floor: ≥ 4.0 ★' : 'Index / ETF exempt'}
            </p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">5Y CAGR</p>
            <p className="text-xl font-bold text-emerald-700 mt-1">
              {cagr5yVal != null ? `${cagr5yVal.toFixed(2)}%` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-500 mt-1">
              Peer percentile:{' '}
              <span className="font-semibold">
                {stats?.pct_cagr_5y != null ? `${stats.pct_cagr_5y}th` : '-'}
              </span>
            </p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">3Y CAGR</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {cagr3yVal != null ? `${cagr3yVal.toFixed(2)}%` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-400 mt-1">
              1Y CAGR: {profile?.cagr_1y != null ? `${profile.cagr_1y.toFixed(2)}%` : 'N/A'}
            </p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">3Y Rolling Return (Median)</p>
            <p className="text-xl font-bold text-indigo-700 mt-1">
              {roll3yVal != null ? `${roll3yVal.toFixed(2)}%` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-500 mt-1">
              Peer pct:{' '}
              <span className="font-semibold">
                {stats?.pct_roll_3y != null ? `${stats.pct_roll_3y}th` : '-'}
              </span>
              {profile?.rolling_consistency_3y != null && (
                <span> • Consistency: {profile.rolling_consistency_3y.toFixed(0)}%</span>
              )}
            </p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Annualized Volatility</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {volVal != null ? `${volVal.toFixed(2)}%` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-400 mt-1">Standard deviation of returns</p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Max Drawdown</p>
            <p className="text-xl font-bold text-red-600 mt-1">
              {mddVal != null ? `${mddVal.toFixed(2)}%` : 'N/A'}
            </p>
            <p className="text-[11px] text-gray-400 mt-1">Peak-to-trough decline</p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Sharpe / Sortino</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {sharpeVal != null ? sharpeVal.toFixed(2) : 'N/A'}{' '}
              <span className="text-sm font-normal text-gray-400">/</span>{' '}
              <span className="text-base font-semibold text-gray-700">
                {sortinoVal != null ? sortinoVal.toFixed(2) : 'N/A'}
              </span>
            </p>
            <p className="text-[11px] text-gray-400 mt-1">Total / downside risk-adjusted</p>
          </div>

          <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
            <p className="text-xs text-gray-500">Alpha / Beta vs Benchmark</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {alphaVal != null ? `${alphaVal >= 0 ? '+' : ''}${alphaVal.toFixed(2)}%` : 'N/A'}{' '}
              <span className="text-sm font-normal text-gray-400">/</span>{' '}
              <span className="text-base font-semibold text-gray-700">
                {betaVal != null ? betaVal.toFixed(2) : 'N/A'}
              </span>
            </p>
            <p className="text-[11px] text-gray-400 mt-1">
              {profile?.tracking_error != null
                ? `Tracking Error: ${profile.tracking_error.toFixed(3)}%`
                : `Track: ${profile?.fund_age_years ?? fund.track_years ?? '-'} yrs`}
            </p>
          </div>

          {(profile?.ytm != null || profile?.modified_duration != null) && (
            <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-100">
              <p className="text-xs text-gray-500">Debt YTM / Mod. Duration</p>
              <p className="text-xl font-bold text-gray-900 mt-1">
                {profile?.ytm != null ? `${profile.ytm.toFixed(2)}%` : 'N/A'}{' '}
                <span className="text-sm font-normal text-gray-400">/</span>{' '}
                <span className="text-base font-semibold text-gray-700">
                  {profile?.modified_duration != null ? `${profile.modified_duration.toFixed(2)}y` : 'N/A'}
                </span>
              </p>
              <p className="text-[11px] text-gray-400 mt-1">Yield to maturity & rate sensitivity</p>
            </div>
          )}
        </section>

        {/* Chart & Sub-Scores Row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Historical NAV Chart */}
          <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 lg:col-span-2">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
              <div>
                <h2 className="text-lg font-semibold flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-indigo-600" />
                  Historical NAV Trajectory
                </h2>
                <p className="text-xs text-gray-500">
                  Computed from {stats?.n_points?.toLocaleString() || navHistory?.length || 0} daily
                  NAV points ({navStart?.date} to {navEnd?.date}) • {navHistory?.length || 0} plotted
                  samples
                </p>
              </div>
              {navStart && navEnd && (
                <div className="text-right">
                  <span className="text-xs text-gray-500">
                    ₹{navStart.nav.toFixed(2)} → ₹{navEnd.nav.toFixed(2)}
                  </span>
                  {totalNavReturn && (
                    <span className="ml-2 px-2 py-0.5 rounded text-xs font-semibold bg-emerald-50 text-emerald-700">
                      +{totalNavReturn}% total
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="h-72 w-full">
              {navHistory && navHistory.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={navHistory} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="navColor" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#4f46e5" stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11, fill: '#64748b' }}
                      tickFormatter={(v) => v.slice(0, 7)}
                      minTickGap={36}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: '#64748b' }}
                      domain={['auto', 'auto']}
                      tickFormatter={(v) => `₹${v}`}
                    />
                    <RechartsTooltip
                      formatter={(val: any) => [`₹${Number(val ?? 0).toFixed(4)}`, 'NAV']}
                      labelFormatter={(label) => `Date: ${label}`}
                    />
                    <Area
                      type="monotone"
                      dataKey="nav"
                      stroke="#4f46e5"
                      strokeWidth={2}
                      fillOpacity={1}
                      fill="url(#navColor)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-gray-400 text-sm">
                  No NAV history available
                </div>
              )}
            </div>
          </section>

          {/* Deterministic Sub-Scores Breakdown */}
          <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <h2 className="text-lg font-semibold flex items-center gap-2 mb-1">
              <BarChart3 className="w-5 h-5 text-indigo-600" />
              Analyst Rubric Sub-Scores
            </h2>
            <p className="text-xs text-gray-500 mb-5">
              Deterministic 0–100 scoring across 5 weighted dimensions
            </p>
            <div className="space-y-4">
              {Object.entries(SUBSCORE_META).map(([key, meta]) => {
                const val = analysis.sub_scores?.[key] ?? 50
                const dynamicWeight = analysis.weights?.[key] ?? meta.defaultWeight
                const weightPct = `${Math.round(dynamicWeight * 100)}%`
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium text-gray-800">
                        {meta.label}{' '}
                        <span className="text-gray-400 font-normal">({weightPct})</span>
                      </span>
                      <span className="font-bold text-gray-900">{val.toFixed(2)} / 100</span>
                    </div>
                    <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          val >= 70
                            ? 'bg-emerald-500'
                            : val >= 50
                              ? 'bg-indigo-500'
                              : 'bg-amber-500'
                        }`}
                        style={{ width: `${Math.min(100, Math.max(0, val))}%` }}
                      />
                    </div>
                    <p className="text-[11px] text-gray-400">{meta.desc}</p>
                  </div>
                )
              })}
            </div>
          </section>
        </div>

        {/* Calendar-Year Returns & Set 2 Red-Flag Checklist Row */}
        {(calendarReturnsData.length > 0 || (checklist && checklist.length > 0)) && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Calendar-Year Returns Bar Chart */}
            {calendarReturnsData.length > 0 && (
              <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                <div className="mb-4">
                  <h2 className="text-lg font-semibold flex items-center gap-2 text-gray-900">
                    <Calendar className="w-5 h-5 text-indigo-600" />
                    Calendar-Year Returns (%)
                  </h2>
                  <p className="text-xs text-gray-500">
                    Year-by-year full-year NAV return performance
                  </p>
                </div>
                <div className="h-60 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={calendarReturnsData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                      <XAxis dataKey="year" tick={{ fontSize: 11, fill: '#64748b' }} />
                      <YAxis
                        tick={{ fontSize: 11, fill: '#64748b' }}
                        tickFormatter={(v) => `${v}%`}
                      />
                      <ReferenceLine y={0} stroke="#cbd5e1" />
                      <RechartsTooltip
                        formatter={(val: any) => [`${Number(val ?? 0).toFixed(2)}%`, 'Return']}
                        labelFormatter={(label) => `Year ${label}`}
                      />
                      <Bar dataKey="returnPct" radius={[4, 4, 0, 0]}>
                        {calendarReturnsData.map((entry, idx) => (
                          <Cell
                            key={idx}
                            fill={entry.returnPct >= 0 ? '#10b981' : '#ef4444'}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>
            )}

            {/* Set 2 Red-Flag Checklist */}
            {checklist && checklist.length > 0 && (
              <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                <div className="flex items-center justify-between gap-2 mb-4">
                  <div>
                    <h2 className="text-lg font-semibold flex items-center gap-2 text-gray-900">
                      <ShieldCheck className="w-5 h-5 text-indigo-600" />
                      Red-Flag & Hygiene Checklist
                    </h2>
                    <p className="text-xs text-gray-500">
                      Deterministic Set 2 · Magic MF structural & category guardrails
                    </p>
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                    {checklist.filter((c: any) => c.passed).length} / {checklist.length} Passed
                  </span>
                </div>
                <div className="space-y-2.5">
                  {checklist.map((item: any) => {
                    const isPass = item.passed
                    const isFail = !isPass && item.severity === 'fail'
                    return (
                      <div
                        key={item.check_id}
                        className={`p-3 rounded-lg border flex items-start justify-between gap-3 text-xs ${
                          isPass
                            ? 'bg-emerald-50/40 border-emerald-100'
                            : isFail
                              ? 'bg-red-50/60 border-red-200'
                              : 'bg-amber-50/60 border-amber-200'
                        }`}
                      >
                        <div className="flex items-start gap-2.5">
                          {isPass ? (
                            <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0 mt-0.5" />
                          ) : isFail ? (
                            <XCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                          ) : (
                            <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
                          )}
                          <div>
                            <p className="font-semibold text-gray-900">{item.label}</p>
                            <p className="text-gray-600 mt-0.5">{item.detail}</p>
                          </div>
                        </div>
                        <span
                          className={`px-2 py-0.5 rounded font-semibold uppercase text-[10px] whitespace-nowrap ${
                            isPass
                              ? 'bg-emerald-100 text-emerald-800'
                              : isFail
                                ? 'bg-red-100 text-red-800'
                                : 'bg-amber-100 text-amber-800'
                          }`}
                        >
                          {isPass ? 'PASS' : item.severity}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </section>
            )}
          </div>
        )}

        {/* Analyst Verdict: Pros, Cons & Change Conditions */}
        {(analysis.pros?.length > 0 ||
          analysis.cons?.length > 0 ||
          analysis.change_conditions?.length > 0) && (
          <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <Award className="w-5 h-5 text-indigo-600" />
                Grounded Analyst Verdict
              </h2>
              {runCtx?.provenance && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700 font-mono">
                    {runCtx.provenance.is_deterministic_fallback
                      ? 'Deterministic Fallback'
                      : `LLM Verdict (${runCtx.provenance.model || 'LLM'})`}
                  </span>
                  {runCtx.provenance.analyst_prompt && (
                    <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-600 font-mono">
                      {runCtx.provenance.analyst_prompt}
                    </span>
                  )}
                  {runCtx.provenance.llm_calls_used != null && (
                    <span className="text-gray-400">
                      LLM Budget: {runCtx.provenance.llm_calls_used}/
                      {runCtx.provenance.llm_calls_budget} calls
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {/* Pros */}
              <div className="bg-emerald-50/50 border border-emerald-100 rounded-xl p-4">
                <h3 className="text-sm font-semibold text-emerald-900 flex items-center gap-2 mb-3">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  Positives & Strengths
                </h3>
                <ul className="space-y-3 text-sm text-gray-800">
                  {analysis.pros?.map((pro: string, i: number) => {
                    const { cleanText, citations } = stripCitations(pro)
                    return (
                      <li key={i} className="space-y-1">
                        <p>{cleanText}</p>
                        {citations.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {citations.map((cid, j) => (
                              <a
                                key={j}
                                href={`#fact-${cid}`}
                                className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 hover:bg-emerald-200"
                              >
                                {cid}
                              </a>
                            ))}
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>

              {/* Cons / Watch */}
              <div className="bg-amber-50/50 border border-amber-100 rounded-xl p-4">
                <h3 className="text-sm font-semibold text-amber-900 flex items-center gap-2 mb-3">
                  <AlertTriangle className="w-4 h-4 text-amber-600" />
                  Risks & Watch Items
                </h3>
                <ul className="space-y-3 text-sm text-gray-800">
                  {analysis.cons?.map((con: string, i: number) => {
                    const { cleanText, citations } = stripCitations(con)
                    return (
                      <li key={i} className="space-y-1">
                        <p>{cleanText}</p>
                        {citations.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {citations.map((cid, j) => (
                              <a
                                key={j}
                                href={`#fact-${cid}`}
                                className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 hover:bg-amber-200"
                              >
                                {cid}
                              </a>
                            ))}
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>

              {/* Change Conditions */}
              <div className="bg-blue-50/50 border border-blue-100 rounded-xl p-4">
                <h3 className="text-sm font-semibold text-blue-900 flex items-center gap-2 mb-3">
                  <RefreshCw className="w-4 h-4 text-blue-600" />
                  When to Re-evaluate
                </h3>
                <ul className="space-y-3 text-sm text-gray-800">
                  {analysis.change_conditions?.length > 0 ? (
                    analysis.change_conditions.map((cond: string, i: number) => {
                      const { cleanText, citations } = stripCitations(cond)
                      return (
                        <li key={i} className="space-y-1">
                          <p>{cleanText}</p>
                          {citations.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                              {citations.map((cid, j) => (
                                <a
                                  key={j}
                                  href={`#fact-${cid}`}
                                  className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 hover:bg-blue-200"
                                >
                                  {cid}
                                </a>
                              ))}
                            </div>
                          )}
                        </li>
                      )
                    })
                  ) : (
                    <li className="text-xs text-gray-500">
                      Monitor quarterly for expense ratio, rating, or benchmark tracking changes.
                    </li>
                  )}
                </ul>
              </div>
            </div>
          </section>
        )}

        {/* Real-Time News & Market Sentiment Intelligence */}
        {news && (
          <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-4">
              <div>
                <h2 className="text-lg font-semibold flex items-center gap-2 text-gray-900">
                  <Newspaper className="w-5 h-5 text-indigo-600" />
                  Real-Time News & Market Sentiment Intelligence
                </h2>
                <p className="text-xs text-gray-500 mt-0.5">
                  Materiality-weighted scheme, AMC, category, and SEBI/RBI news signals factored into the Analyst Rubric
                </p>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="px-2.5 py-1 rounded-lg bg-indigo-50 border border-indigo-100 text-indigo-800 font-semibold">
                  News Score: {Number(news.news_score ?? 50).toFixed(1)}/100
                </span>
                <span className="px-2.5 py-1 rounded-lg bg-purple-50 border border-purple-100 text-purple-800 font-semibold">
                  Sentiment Score: {Number(news.sentiment_score ?? 50).toFixed(1)}/100
                </span>
                <span
                  className={`px-2.5 py-1 rounded-lg font-semibold uppercase ${
                    news.overall_sentiment === 'positive'
                      ? 'bg-emerald-100 text-emerald-800'
                      : news.overall_sentiment === 'negative'
                        ? 'bg-red-100 text-red-800'
                        : 'bg-gray-100 text-gray-700'
                  }`}
                >
                  {news.overall_sentiment || 'neutral'}
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {news.items?.map((item: any, idx: number) => (
                <div
                  key={item.id || idx}
                  className="p-3.5 rounded-lg border border-gray-200 bg-gray-50/50 space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2 text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`px-1.5 py-0.5 rounded font-semibold uppercase ${
                          item.sentiment === 'positive'
                            ? 'bg-emerald-100 text-emerald-800'
                            : item.sentiment === 'negative'
                              ? 'bg-red-100 text-red-800'
                              : 'bg-gray-200 text-gray-700'
                        }`}
                      >
                        {item.sentiment}
                      </span>
                      <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 font-mono uppercase">
                        {item.materiality} materiality
                      </span>
                    </div>
                    <span className="text-gray-400 font-mono">
                      {item.source} • {item.published_at}
                    </span>
                  </div>
                  <p className="text-xs font-semibold text-gray-900 leading-snug">{item.title}</p>
                  <p className="text-xs text-gray-600 leading-relaxed">{item.summary}</p>
                  {item.url && item.url.startsWith('http') && (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-[11px] text-indigo-600 hover:underline pt-0.5"
                    >
                      Source Link <ExternalLink className="w-2.5 h-2.5" />
                    </a>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Retrieved RAG Documents (Fact Sheets & Circulars) */}
        {documents && documents.length > 0 && (
          <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <h2 className="text-lg font-semibold flex items-center gap-2 mb-4">
              <FileText className="w-5 h-5 text-indigo-600" />
              Retrieved Fact-Sheet & Regulatory Documents (RAG Evidence)
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {documents.map((doc: any, i: number) => (
                <div key={i} className="p-4 rounded-lg border border-gray-200 bg-gray-50/60 space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-semibold uppercase tracking-wider px-2 py-0.5 rounded bg-indigo-100 text-indigo-800">
                      {doc.doc_type || 'Document'} • {doc.section || 'Overview'}
                    </span>
                    <div className="flex items-center gap-2 text-gray-500">
                      {doc.score != null && (
                        <span className="font-mono text-[11px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600">
                          Similarity: {(doc.score * 100).toFixed(1)}%
                        </span>
                      )}
                      <span>As of {doc.as_of}</span>
                    </div>
                  </div>
                  <p className="text-xs text-gray-700 whitespace-pre-line leading-relaxed">
                    {doc.text}
                  </p>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-gray-400 font-mono">
                    <span>Ref: {doc.source_url || doc.doc_id}</span>
                    {doc.doc_id && doc.source_url && <span>Chunk ID: {doc.doc_id}</span>}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Complete Grounded Facts & Citations Ledger */}
        <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <ShieldCheck className="w-5 h-5 text-indigo-600" />
                Grounded Facts & Audit Citations Ledger
              </h2>
              <p className="text-xs text-gray-500">
                Every number or claim used by the Analyst and Decision Maker is verified against
                these cited facts.
              </p>
            </div>
            <span className="text-xs font-medium bg-gray-100 text-gray-700 px-2.5 py-1 rounded-full">
              {facts?.length || 0} Verified Facts
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="text-gray-500 border-b border-gray-200">
                  <th className="py-2.5 pr-4 font-medium whitespace-nowrap">Fact ID</th>
                  <th className="py-2.5 px-3 font-medium whitespace-nowrap">Field</th>
                  <th className="py-2.5 px-3 font-medium">Value</th>
                  <th className="py-2.5 px-3 font-medium whitespace-nowrap">Citation Source</th>
                  <th className="py-2.5 px-3 font-medium whitespace-nowrap">As-Of Date</th>
                  <th className="py-2.5 pl-3 font-medium">Reference / Snippet</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 align-top">
                {facts?.map((f: any) => (
                  <tr key={f.id} id={`fact-${f.id}`} className="hover:bg-indigo-50/40 transition">
                    <td className="py-3 pr-4 font-mono text-indigo-700 font-medium whitespace-nowrap">
                      {f.id}
                    </td>
                    <td className="py-3 px-3 text-gray-700 font-medium whitespace-nowrap">
                      {f.name}
                    </td>
                    <td className="py-3 px-3 text-gray-900 font-semibold break-words min-w-[140px] max-w-md whitespace-pre-line">
                      {String(f.value)}
                    </td>
                    <td className="py-3 px-3 whitespace-nowrap">
                      <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700 font-mono">
                        {f.citation?.source}
                      </span>
                    </td>
                    <td className="py-3 px-3 text-gray-600 whitespace-nowrap">
                      {f.citation?.as_of}
                    </td>
                    <td className="py-3 pl-3 text-gray-500 break-words min-w-[200px] max-w-lg whitespace-pre-line">
                      {f.citation?.snippet ? (
                        <div className="space-y-0.5">
                          <p className="text-gray-600">{f.citation.snippet}</p>
                          {f.citation.url_or_doc_id && (
                            <p className="font-mono text-[10px] text-gray-400">
                              Doc: {f.citation.url_or_doc_id}
                            </p>
                          )}
                        </div>
                      ) : (
                        <span className="font-mono">{f.citation?.url_or_doc_id || '-'}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Compliance Disclosures (if present in run context) */}
        {runCtx?.disclosures && runCtx.disclosures.length > 0 && (
          <section className="bg-gray-100 p-5 rounded-xl border border-gray-200 text-xs text-gray-500">
            <h3 className="font-semibold text-gray-700 mb-2 uppercase tracking-wider">
              Regulatory & Compliance Disclosures
            </h3>
            <ul className="list-disc pl-5 space-y-1">
              {runCtx.disclosures.map((d: string, i: number) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </section>
        )}
    </div>
  )

  if (embedded) {
    return content
  }

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 font-sans p-4 md:p-8">
      {content}
    </div>
  )
}
