import { useState } from 'react'
import axios from 'axios'
import { useMutation } from '@tanstack/react-query'
import {
  ArrowLeftRight,
  Trophy,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  MinusCircle,
  BarChart3,
  TrendingUp,
  Scale,
  Sparkles,
  ExternalLink,
} from 'lucide-react'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import StockCombobox, { type StockSearchOption } from './StockCombobox'
import StockSyncPanel from './StockSyncPanel'
import Stock360Button from './Stock360Report'

const API_BASE = 'http://localhost:8000/api'

interface StockCompareProps {
  onAnalyzeStock?: (symbol: string) => void
}

const PRESET_PAIRS = [
  {
    label: 'IT Leaders: TCS vs INFY',
    a: { symbol: 'TCS', name: 'Tata Consultancy Services Limited', sector: 'Information Technology', is_bank: false },
    b: { symbol: 'INFY', name: 'Infosys Limited', sector: 'Information Technology', is_bank: false },
  },
  {
    label: 'Private Banks: HDFCBANK vs ICICIBANK',
    a: { symbol: 'HDFCBANK', name: 'HDFC Bank Limited', sector: 'Financial Services', is_bank: true },
    b: { symbol: 'ICICIBANK', name: 'ICICI Bank Limited', sector: 'Financial Services', is_bank: true },
  },
  {
    label: 'FMCG Giants: HINDUNILVR vs ITC',
    a: { symbol: 'HINDUNILVR', name: 'Hindustan Unilever Limited', sector: 'Fast Moving Consumer Goods', is_bank: false },
    b: { symbol: 'ITC', name: 'ITC Limited', sector: 'Fast Moving Consumer Goods', is_bank: false },
  },
]

export default function StockCompare({ onAnalyzeStock }: StockCompareProps) {
  const [stockA, setStockA] = useState<StockSearchOption | null>(PRESET_PAIRS[0].a)
  const [stockB, setStockB] = useState<StockSearchOption | null>(PRESET_PAIRS[0].b)
  const [ruleSet, setRuleSet] = useState<'set1' | 'set2'>('set2')
  const [minMarketCapInput, setMinMarketCapInput] = useState<string>('')
  const [includeNarrative, setIncludeNarrative] = useState(true)

  const compareMutation = useMutation({
    mutationFn: async (params: {
      symA: string
      symB: string
      rs: 'set1' | 'set2'
      minCap?: number
      narrative: boolean
    }) => {
      const res = await axios.post(`${API_BASE}/stocks/compare`, {
        symbols: [params.symA, params.symB],
        stock_rule_set: params.rs,
        min_market_cap_cr: params.minCap,
        narrative: params.narrative,
      })
      return res.data
    },
  })

  const handleCompare = () => {
    if (!stockA || !stockB) return
    const minCap =
      minMarketCapInput.trim() !== '' && !Number.isNaN(Number(minMarketCapInput))
        ? Number(minMarketCapInput)
        : undefined
    compareMutation.mutate({
      symA: stockA.symbol,
      symB: stockB.symbol,
      rs: ruleSet,
      minCap,
      narrative: includeNarrative,
    })
  }

  const handleSwap = () => {
    const prevA = stockA
    const prevB = stockB
    setStockA(prevB)
    setStockB(prevA)
    if (prevA && prevB) {
      const minCap =
        minMarketCapInput.trim() !== '' && !Number.isNaN(Number(minMarketCapInput))
          ? Number(minMarketCapInput)
          : undefined
      compareMutation.mutate({
        symA: prevB.symbol,
        symB: prevA.symbol,
        rs: ruleSet,
        minCap,
        narrative: includeNarrative,
      })
    }
  }

  const data = compareMutation.data
  const symA = data?.symbols?.[0] || stockA?.symbol || 'Stock A'
  const symB = data?.symbols?.[1] || stockB?.symbol || 'Stock B'
  const evalA = data?.evaluations?.[symA]
  const evalB = data?.evaluations?.[symB]
  const valA = data?.valuations?.[symA]
  const valB = data?.valuations?.[symB]

  return (
    <div className="space-y-6">
      <StockSyncPanel compact />

      <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
              <Scale className="w-5 h-5 text-indigo-600" />
              Head-to-Head Stock Fundamental Comparison
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Compare any two NSE equities across 10-Pointer / 6-Pointer checklists, 5Y CAGR, ROE/ROCE, CFO/PAT, and Relative Valuation.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {PRESET_PAIRS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => {
                  setStockA(preset.a)
                  setStockB(preset.b)
                }}
                className="text-xs px-2.5 py-1 rounded-full border border-indigo-200 bg-indigo-50/60 text-indigo-700 hover:bg-indigo-100 transition font-medium"
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        {/* Stock Pickers + Swap */}
        <div className="grid grid-cols-1 lg:grid-cols-11 gap-4 items-end">
          <div className="lg:col-span-5">
            <StockCombobox
              label="Stock A"
              value={stockA}
              onChange={setStockA}
              excludeSymbols={stockB ? [stockB.symbol] : []}
              ruleSet={ruleSet}
            />
          </div>

          <div className="lg:col-span-1 flex justify-center pb-1">
            <button
              type="button"
              onClick={handleSwap}
              className="p-2.5 rounded-xl border border-gray-300 bg-gray-50 hover:bg-indigo-50 hover:border-indigo-300 text-gray-700 hover:text-indigo-700 transition"
              title="Swap Stock A and Stock B"
            >
              <ArrowLeftRight className="w-4 h-4" />
            </button>
          </div>

          <div className="lg:col-span-5">
            <StockCombobox
              label="Stock B"
              value={stockB}
              onChange={setStockB}
              excludeSymbols={stockA ? [stockA.symbol] : []}
              ruleSet={ruleSet}
            />
          </div>
        </div>

        {/* Rule Set & Optional Market Cap Filter Controls */}
        <div className="flex flex-wrap items-end justify-between gap-4 pt-2 border-t border-gray-100">
          <div className="flex flex-wrap items-center gap-4">
            <div>
              <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-600 mb-1">
                Stock Rule Set
              </label>
              <div className="inline-flex rounded-lg border border-gray-300 p-0.5 bg-gray-50">
                <button
                  type="button"
                  onClick={() => setRuleSet('set2')}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition ${
                    ruleSet === 'set2'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'text-gray-600 hover:text-gray-900'
                  }`}
                >
                  Set 2: FA Course Notes (10-Pt / 6-Pt)
                </button>
                <button
                  type="button"
                  onClick={() => setRuleSet('set1')}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition ${
                    ruleSet === 'set1'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'text-gray-600 hover:text-gray-900'
                  }`}
                >
                  Set 1: Equity Heuristics
                </button>
              </div>
            </div>

            <div>
              <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-600 mb-1">
                Min Market Cap (₹ Cr, optional)
              </label>
              <input
                type="number"
                min="0"
                placeholder="No limit (leave empty)"
                value={minMarketCapInput}
                onChange={(e) => setMinMarketCapInput(e.target.value)}
                className="w-48 rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 focus:border-indigo-500 focus:outline-none"
              />
            </div>

            <label className="inline-flex items-center gap-1.5 text-xs text-gray-700 cursor-pointer pb-1.5">
              <input
                type="checkbox"
                checked={includeNarrative}
                onChange={(e) => setIncludeNarrative(e.target.checked)}
                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              <span>Include AI Analyst Verdict</span>
            </label>
          </div>

          <button
            type="button"
            onClick={handleCompare}
            disabled={!stockA || !stockB || compareMutation.isPending}
            className="px-5 py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition shadow-sm"
          >
            {compareMutation.isPending ? 'Comparing...' : 'Compare Stocks'}
          </button>
        </div>
      </div>

      {compareMutation.isError && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {(compareMutation.error as any)?.response?.data?.detail ||
            'Failed to compare stocks. Please try again.'}
        </div>
      )}

      {data && (
        <div className="space-y-6">
          {/* Cross-sector warning */}
          {data.cross_sector_warning && (
            <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-amber-900 font-medium">
                {data.cross_sector_warning}
              </div>
            </div>
          )}

          {/* Winner Banner + Summary Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="rounded-2xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-white p-5 flex flex-col justify-between">
              <div className="flex items-center gap-2 text-indigo-700 text-xs font-bold uppercase tracking-wider">
                <Trophy className="w-4 h-4" />
                Overall Fundamental Leader
              </div>
              <div className="my-3">
                <div className="text-2xl font-extrabold text-gray-900">
                  {data.overall_winner || 'Tie'}
                </div>
                <div className="text-xs text-gray-600 mt-1">
                  Criterion Wins: <span className="font-semibold">{symA}: {data.wins_by_symbol?.[symA] ?? 0}</span> vs{' '}
                  <span className="font-semibold">{symB}: {data.wins_by_symbol?.[symB] ?? 0}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {[symA, symB].map((sy) => (
                    <Stock360Button key={sy} symbol={sy} context="compare" />
                  ))}
                </div>
              </div>
              <div className="text-[11px] text-gray-500">
                Matrix: <span className="font-semibold uppercase">{data.matrix_type}</span> · Rule Set:{' '}
                <span className="font-semibold uppercase">{data.rule_set}</span>
              </div>
            </div>

            {(data.stocks || []).map((st: any) => (
              <div
                key={st.symbol}
                className={`rounded-2xl border p-5 ${
                  data.overall_winner === st.symbol
                    ? 'border-emerald-300 bg-emerald-50/30'
                    : 'border-gray-200 bg-white'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-base text-indigo-700">
                        {st.symbol}
                      </span>
                      <span
                        className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                          st.passed
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-rose-100 text-rose-800'
                        }`}
                      >
                        {st.passed ? 'Fundamentally Good' : 'Fails Screen'} ({st.passed_count}/{st.evaluated_count})
                      </span>
                    </div>
                    <div className="text-xs font-medium text-gray-800 mt-0.5">{st.name}</div>
                    <div className="text-[11px] text-gray-500">{st.sector}</div>
                  </div>
                  {onAnalyzeStock && (
                    <button
                      type="button"
                      onClick={() => onAnalyzeStock(st.symbol)}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                    >
                      <span>Analyse</span>
                      <ExternalLink className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                <div className="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-gray-100 text-xs">
                  <div>
                    <div className="text-[10px] text-gray-500 uppercase">Price</div>
                    <div className="font-bold text-gray-900">
                      ₹{Number(st.price || 0).toLocaleString('en-IN')}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-gray-500 uppercase">Composite Score</div>
                    <div className="font-bold text-indigo-700">{st.composite_score}/100</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-gray-500 uppercase">Valuation</div>
                    <div className="font-semibold text-gray-800 capitalize">
                      {(st.verdict || '').replace('_', ' ')}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* 10-Pointer / 6-Pointer Checklists Side-by-Side */}
          {evalA && evalB && (
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
              <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800">
                Screening Checklist Side-by-Side ({data.rule_set.toUpperCase()})
              </h3>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {[
                  { sym: symA, ev: evalA },
                  { sym: symB, ev: evalB },
                ].map(({ sym, ev }) => (
                  <div key={sym} className="border border-gray-200 rounded-xl overflow-hidden">
                    <div className="bg-gray-50 px-4 py-2.5 border-b border-gray-200 flex items-center justify-between">
                      <span className="font-mono font-bold text-sm text-gray-900">{sym}</span>
                      <span className="text-xs font-semibold text-gray-600">
                        {ev.passed_count}/{ev.evaluated_count} Passed · Score {ev.composite_score}
                      </span>
                    </div>
                    <div className="divide-y divide-gray-100">
                      {(ev.checklist || []).map((item: any) => (
                        <div
                          key={item.id}
                          className="px-4 py-2 flex items-center justify-between gap-2 text-xs"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            {item.status === 'pass' ? (
                              <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
                            ) : item.status === 'fail' ? (
                              <XCircle className="w-4 h-4 text-rose-600 flex-shrink-0" />
                            ) : (
                              <MinusCircle className="w-4 h-4 text-gray-400 flex-shrink-0" />
                            )}
                            <span className="font-medium text-gray-800 truncate">
                              {item.label}
                            </span>
                          </div>
                          <div className="text-right flex-shrink-0">
                            <span
                              className={`font-mono text-[11px] px-1.5 py-0.5 rounded ${
                                item.status === 'pass'
                                  ? 'bg-emerald-50 text-emerald-700'
                                  : item.status === 'fail'
                                  ? 'bg-rose-50 text-rose-700'
                                  : 'bg-gray-100 text-gray-500'
                              }`}
                            >
                              {item.status === 'not_applied'
                                ? 'Not applied'
                                : item.status === 'no_data'
                                ? 'No data'
                                : item.actual !== null && item.actual !== undefined
                                ? String(item.actual)
                                : item.status}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Criteria Scorecard Table */}
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
            <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800">
              Head-to-Head Fundamental Scorecard ({data.criteria_matrix?.length || 0} Criteria)
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-gray-200 text-gray-500 uppercase text-[11px]">
                    <th className="py-2.5 px-3">Criterion</th>
                    <th className="py-2.5 px-3">Category</th>
                    <th className="py-2.5 px-3 text-right">{symA}</th>
                    <th className="py-2.5 px-3 text-right">{symB}</th>
                    <th className="py-2.5 px-3 text-center">Advantage</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(data.criteria_matrix || []).map((row: any) => (
                    <tr key={row.key} className="hover:bg-gray-50/70">
                      <td className="py-2.5 px-3 font-medium text-gray-900">{row.label}</td>
                      <td className="py-2.5 px-3 text-gray-500 capitalize">
                        {(row.category || '').replace('_', ' ')}
                      </td>
                      <td
                        className={`py-2.5 px-3 text-right font-mono ${
                          row.winner_symbol === symA
                            ? 'font-bold text-emerald-700 bg-emerald-50/40'
                            : 'text-gray-800'
                        }`}
                      >
                        {row.formatted?.[symA] ?? 'N/A'}
                      </td>
                      <td
                        className={`py-2.5 px-3 text-right font-mono ${
                          row.winner_symbol === symB
                            ? 'font-bold text-emerald-700 bg-emerald-50/40'
                            : 'text-gray-800'
                        }`}
                      >
                        {row.formatted?.[symB] ?? 'N/A'}
                      </td>
                      <td className="py-2.5 px-3 text-center">
                        {row.winner_symbol ? (
                          <span className="font-mono font-bold text-[11px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">
                            {row.winner_symbol}
                          </span>
                        ) : (
                          <span className="text-gray-400 text-[11px]">Tie / N/A</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Charts: 5Y Revenue/PAT Trend & Normalised Price Chart */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm space-y-3">
              <div className="flex items-center gap-2 text-sm font-bold text-gray-800">
                <BarChart3 className="w-4 h-4 text-indigo-600" />
                5-Year PAT Trend (₹ Cr)
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.financial_growth_series || []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey={`${symA}_pat_cr`} name={`${symA} PAT (₹ Cr)`} fill="#4f46e5" radius={[4, 4, 0, 0]} />
                    <Bar dataKey={`${symB}_pat_cr`} name={`${symB} PAT (₹ Cr)`} fill="#10b981" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm space-y-3">
              <div className="flex items-center gap-2 text-sm font-bold text-gray-800">
                <TrendingUp className="w-4 h-4 text-indigo-600" />
                Normalised 1Y Price Performance (Base = 100)
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.normalized_price_series || []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={24} />
                    <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey={symA}
                      stroke="#4f46e5"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey={symB}
                      stroke="#10b981"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* Relative Valuation Comparison Table */}
          {valA && valB && (
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
              <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800">
                Weighted Relative Valuation vs Sector Medians (FA Course Notes Sec 5)
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[
                  { sym: symA, val: valA },
                  { sym: symB, val: valB },
                ].map(({ sym, val }) => (
                  <div key={sym} className="rounded-xl border border-gray-200 p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-bold text-sm text-indigo-700">{sym}</span>
                      <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 capitalize">
                        {(val.verdict || '').replace('_', ' ')}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-xs pt-1">
                      <div>
                        <span className="text-gray-500 block text-[11px]">Current Price</span>
                        <span className="font-bold">₹{Number(val.cmp || 0).toLocaleString('en-IN')}</span>
                      </div>
                      <div>
                        <span className="text-gray-500 block text-[11px]">Implied Base</span>
                        <span className="font-bold">
                          {val.fair_value_low != null ? `₹${val.fair_value_low.toLocaleString('en-IN')}` : 'N/A'}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-500 block text-[11px]">+20% Fair High</span>
                        <span className="font-bold">
                          {val.fair_value_high != null ? `₹${val.fair_value_high.toLocaleString('en-IN')}` : 'N/A'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Narrative Verdict */}
          {data.narrative && (
            <div className="rounded-2xl border border-indigo-200 bg-indigo-50/30 p-6 space-y-2">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-indigo-800">
                <Sparkles className="w-4 h-4 text-indigo-600" />
                Fundamental Comparison Synthesis
              </div>
              <div className="text-sm text-gray-800 whitespace-pre-line leading-relaxed">
                {data.narrative}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
