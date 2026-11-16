import { useState, useEffect } from 'react'
import axios from 'axios'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  CheckCircle2,
  XCircle,
  MinusCircle,
  AlertTriangle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Sparkles,
  Building2,
  BarChart3,
  Scale,
  Sliders,
} from 'lucide-react'
import {
  ResponsiveContainer,
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
import { Stock360Panel } from './Stock360Report'
import { useAppStore } from '../store/useAppStore'

const API_BASE = 'http://localhost:8000/api'

const RATIO_META: Record<
  string,
  { label: string; formula: string; group: string; format: 'pct_dec' | 'pct' | 'x' | 'cr' }
> = {
  roe: {
    label: 'Return on Equity (ROE)',
    formula: 'PAT / Net Worth',
    group: 'Profitability',
    format: 'pct_dec',
  },
  roce: {
    label: 'Return on Capital Employed (ROCE)',
    formula: 'EBIT / Capital Employed',
    group: 'Profitability',
    format: 'pct_dec',
  },
  ebitda_margin: {
    label: 'EBITDA Margin',
    formula: 'EBITDA / Revenue',
    group: 'Profitability',
    format: 'pct_dec',
  },
  pat_margin: {
    label: 'PAT Margin',
    formula: 'PAT / Revenue',
    group: 'Profitability',
    format: 'pct_dec',
  },
  debt_to_equity: {
    label: 'Debt to Equity (D/E)',
    formula: 'Total Debt / Net Worth (Optimal < 2.0x)',
    group: 'Leverage & Liquidity',
    format: 'x',
  },
  interest_coverage: {
    label: 'Interest Coverage Ratio',
    formula: 'EBIT / Finance Cost',
    group: 'Leverage & Liquidity',
    format: 'x',
  },
  current_ratio: {
    label: 'Current Ratio',
    formula: 'Current Assets / Current Liabilities (Ideal 1.33x–3.0x)',
    group: 'Leverage & Liquidity',
    format: 'x',
  },
  cfo_to_pat: {
    label: 'Cumulative CFO / PAT',
    formula: '5Y Operating Cash Flow / 5Y PAT (Target ≥ 0.80x)',
    group: 'Cash Flow & Efficiency',
    format: 'x',
  },
  fcf_cr: {
    label: 'Free Cash Flow (FCF)',
    formula: 'Operating Cash Flow − CapEx',
    group: 'Cash Flow & Efficiency',
    format: 'cr',
  },
  asset_turnover: {
    label: 'Asset Turnover',
    formula: 'Revenue / Total Assets',
    group: 'Cash Flow & Efficiency',
    format: 'x',
  },
  pe: {
    label: 'Price to Earnings (P/E)',
    formula: 'Price / EPS',
    group: 'Valuation & Dividend',
    format: 'x',
  },
  pb: {
    label: 'Price to Book (P/B)',
    formula: 'Price / Book Value per Share',
    group: 'Valuation & Dividend',
    format: 'x',
  },
  ev_ebitda: {
    label: 'EV / EBITDA',
    formula: '(Market Cap + Debt − Cash) / EBITDA',
    group: 'Valuation & Dividend',
    format: 'x',
  },
  dividend_yield: {
    label: 'Dividend Yield',
    formula: 'Dividend per Share / Price',
    group: 'Valuation & Dividend',
    format: 'pct_dec',
  },
  nim_pct: {
    label: 'Net Interest Margin (NIM)',
    formula: 'Net Interest Income / Avg Earning Assets',
    group: 'Banking Key Metrics',
    format: 'pct',
  },
  gnpa_pct: {
    label: 'Gross NPA %',
    formula: 'Gross NPAs / Gross Advances (Target ≤ 3%)',
    group: 'Banking Key Metrics',
    format: 'pct',
  },
  nnpa_pct: {
    label: 'Net NPA %',
    formula: 'Net NPAs / Net Advances (Target ≤ 1%)',
    group: 'Banking Key Metrics',
    format: 'pct',
  },
  casa_pct: {
    label: 'CASA Ratio %',
    formula: '(Current + Savings Deposits) / Total Deposits (Target ≥ 40%)',
    group: 'Banking Key Metrics',
    format: 'pct',
  },
  car_pct: {
    label: 'Capital Adequacy Ratio (CAR %)',
    formula: '(Tier 1 + Tier 2 Capital) / Risk-Weighted Assets (Target ≥ 12%)',
    group: 'Banking Key Metrics',
    format: 'pct',
  },
}

function formatRatioValue(val: number | null | undefined, fmt: 'pct_dec' | 'pct' | 'x' | 'cr'): string {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return 'N/A'
  const n = Number(val)
  if (fmt === 'pct_dec') return `${(n * 100).toFixed(1)}%`
  if (fmt === 'pct') return `${n.toFixed(2)}%`
  if (fmt === 'cr') return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`
  return `${n.toFixed(2)}x`
}

export default function StockAnalyzer() {
  const { selectedAnalyzeSymbol, setSelectedAnalyzeSymbol } = useAppStore()
  const [selectedStock, setSelectedStock] = useState<StockSearchOption | null>({
    symbol: selectedAnalyzeSymbol || 'INFY',
    name: selectedAnalyzeSymbol === 'HDFCBANK' ? 'HDFC Bank Limited' : 'Infosys Limited',
    sector: selectedAnalyzeSymbol === 'HDFCBANK' ? 'Financial Services' : 'Information Technology',
    is_bank: selectedAnalyzeSymbol === 'HDFCBANK',
  })
  const [ruleSet, setRuleSet] = useState<'set1' | 'set2'>('set2')
  const [minMarketCapInput, setMinMarketCapInput] = useState<string>('')
  const [analyzerTab, setAnalyzerTab] = useState<'analysis' | 'report360'>('analysis')
  const [showEicSection, setShowEicSection] = useState(false)
  const [showOverridesSection, setShowOverridesSection] = useState(false)

  // Manual overrides state
  const [fiiInput, setFiiInput] = useState<string>('')
  const [pledgeInput, setPledgeInput] = useState<string>('')
  const [nimInput, setNimInput] = useState<string>('')
  const [gnpaInput, setGnpaInput] = useState<string>('')
  const [nnpaInput, setNnpaInput] = useState<string>('')
  const [casaInput, setCasaInput] = useState<string>('')
  const [carInput, setCarInput] = useState<string>('')

  useEffect(() => {
    if (selectedAnalyzeSymbol && selectedAnalyzeSymbol !== selectedStock?.symbol) {
      setSelectedStock({
        symbol: selectedAnalyzeSymbol,
        name: selectedAnalyzeSymbol,
        sector: 'NSE Equity',
        is_bank: selectedAnalyzeSymbol.includes('BANK') || selectedAnalyzeSymbol === 'SBIN',
      })
    }
  }, [selectedAnalyzeSymbol, selectedStock?.symbol])

  const buildOverridesPayload = () => {
    const ov: Record<string, number> = {}
    if (fiiInput.trim() !== '') ov.fii_holding_pct = Number(fiiInput)
    if (pledgeInput.trim() !== '') ov.promoter_pledge_pct = Number(pledgeInput)
    if (nimInput.trim() !== '') ov.nim_pct = Number(nimInput)
    if (gnpaInput.trim() !== '') ov.gnpa_pct = Number(gnpaInput)
    if (nnpaInput.trim() !== '') ov.nnpa_pct = Number(nnpaInput)
    if (casaInput.trim() !== '') ov.casa_pct = Number(casaInput)
    if (carInput.trim() !== '') ov.car_pct = Number(carInput)
    return Object.keys(ov).length > 0 ? ov : undefined
  }

  const analyzeQuery = useQuery({
    queryKey: [
      'stockAnalyze',
      selectedStock?.symbol,
      ruleSet,
      minMarketCapInput,
      fiiInput,
      pledgeInput,
      nimInput,
      gnpaInput,
      nnpaInput,
      casaInput,
      carInput,
    ],
    queryFn: async () => {
      const sym = selectedStock?.symbol || 'INFY'
      const minCap =
        minMarketCapInput.trim() !== '' && !Number.isNaN(Number(minMarketCapInput))
          ? Number(minMarketCapInput)
          : undefined
      const res = await axios.post(`${API_BASE}/stocks/analyze`, {
        symbol: sym,
        stock_rule_set: ruleSet,
        min_market_cap_cr: minCap,
        narrative: true,
        overrides: buildOverridesPayload(),
      })
      return res.data
    },
    enabled: Boolean(selectedStock?.symbol),
  })

  const refreshMutation = useMutation({
    mutationFn: async () => {
      if (!selectedStock?.symbol) return
      await axios.post(`${API_BASE}/stocks/${selectedStock.symbol}/refresh`)
      await analyzeQuery.refetch()
    },
  })

  const data = analyzeQuery.data
  const ov = data?.overview
  const activeScreen = data?.screening?.active
  const ratios = data?.ratios || {}
  const valuation = data?.valuation || {}
  const statements = data?.financial_statements || []
  const eic = data?.eic

  return (
    <div className="space-y-6">
      <StockSyncPanel compact />

      {/* Top Picker & Controls */}
      <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-end">
          <div className="lg:col-span-6">
            <StockCombobox
              label="Select NSE Stock to Analyse"
              value={selectedStock}
              onChange={(st) => {
                setSelectedStock(st)
                if (st?.symbol) setSelectedAnalyzeSymbol(st.symbol)
              }}
              ruleSet={ruleSet}
            />
          </div>

          <div className="lg:col-span-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-600 mb-1">
              Rule Set
            </label>
            <div className="inline-flex rounded-lg border border-gray-300 p-0.5 bg-gray-50 w-full">
              <button
                type="button"
                onClick={() => setRuleSet('set2')}
                className={`flex-1 px-2.5 py-1.5 text-xs font-semibold rounded-md transition ${
                  ruleSet === 'set2'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Set 2 (FA Notes)
              </button>
              <button
                type="button"
                onClick={() => setRuleSet('set1')}
                className={`flex-1 px-2.5 py-1.5 text-xs font-semibold rounded-md transition ${
                  ruleSet === 'set1'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Set 1 (Heuristics)
              </button>
            </div>
          </div>

          <div className="lg:col-span-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-600 mb-1">
              Min Market Cap (₹ Cr, optional)
            </label>
            <input
              type="number"
              min="0"
              placeholder="Leave empty for no limit"
              value={minMarketCapInput}
              onChange={(e) => setMinMarketCapInput(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 focus:border-indigo-500 focus:outline-none"
            />
          </div>
        </div>

        {/* Quick ticker pills */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span className="text-[11px] text-gray-500 font-medium">Quick Pick:</span>
          {['INFY', 'TCS', 'HDFCBANK', 'ICICIBANK', 'HINDUNILVR', 'ITC', 'MARUTI', 'SUNPHARMA', 'WEAKCORP'].map(
            (sym) => (
              <button
                key={sym}
                type="button"
                onClick={() => {
                  setSelectedStock({
                    symbol: sym,
                    name: sym,
                    sector: 'NSE Equity',
                    is_bank: sym.includes('BANK'),
                  })
                  setSelectedAnalyzeSymbol(sym)
                }}
                className={`text-xs font-mono px-2.5 py-0.5 rounded-full border transition ${
                  selectedStock?.symbol === sym
                    ? 'bg-indigo-600 text-white border-indigo-600 font-bold'
                    : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-indigo-50'
                }`}
              >
                {sym}
              </button>
            )
          )}
        </div>
      </div>

      <div role="tablist" className="flex gap-1 border-b border-gray-200">
        {([['analysis', 'Analysis'], ['report360', '360° Report']] as const).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={analyzerTab === id}
            type="button"
            onClick={() => setAnalyzerTab(id)}
            className={`px-4 py-2 text-xs font-semibold border-b-2 -mb-px transition ${
              analyzerTab === id ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {analyzerTab === 'report360' && selectedStock?.symbol && (
        <div className="h-[75vh] overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          <Stock360Panel key={selectedStock.symbol} symbol={selectedStock.symbol} context="analyzer" />
        </div>
      )}
      {analyzerTab === 'report360' && !selectedStock?.symbol && (
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">Select a stock to open its 360° report.</div>
      )}

      {analyzerTab === 'analysis' && analyzeQuery.isLoading && (
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">
          Computing 5-year ratios, checklist evaluation, and relative valuation...
        </div>
      )}

      {analyzerTab === 'analysis' && data && ov && activeScreen && (
        <div className="space-y-6">
          {/* 1. Header Card with Fundamentally Good Verdict Badge & As-Of Date */}
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap items-center gap-2.5">
                  <span className="font-mono text-2xl font-extrabold text-indigo-700">
                    {ov.symbol}
                  </span>
                  <h2 className="text-xl font-bold text-gray-900">{ov.name}</h2>
                  <span
                    className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold ${
                      activeScreen.passed
                        ? 'bg-emerald-100 text-emerald-800 border border-emerald-300'
                        : 'bg-rose-100 text-rose-800 border border-rose-300'
                    }`}
                  >
                    {activeScreen.passed ? (
                      <CheckCircle2 className="w-3.5 h-3.5" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5" />
                    )}
                    {activeScreen.passed ? 'Fundamentally Good' : 'Fails Fundamental Screen'} (
                    {activeScreen.passed_count}/{activeScreen.evaluated_count})
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-3 mt-1.5 text-xs text-gray-600">
                  <span className="inline-flex items-center gap-1 font-medium text-indigo-700 bg-indigo-50 px-2.5 py-0.5 rounded-full">
                    <Building2 className="w-3.5 h-3.5" />
                    {ov.sector} · {ov.industry}
                  </span>
                  {ov.is_bank && (
                    <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold text-[11px]">
                      Banking / NBFC Framework (6-Pointer)
                    </span>
                  )}
                  <span>As of: {ov.as_of_date}</span>
                  <span>Source: {ov.source}</span>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className="text-right">
                  <div className="text-xs text-gray-500 uppercase">Current Price</div>
                  <div className="text-2xl font-extrabold text-gray-900">
                    ₹{Number(ov.price || 0).toLocaleString('en-IN')}
                  </div>
                  <div className="text-xs text-gray-500">
                    MCap: ₹{Number(ov.market_cap_cr || 0).toLocaleString('en-IN')} Cr
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => refreshMutation.mutate()}
                  disabled={refreshMutation.isPending}
                  className="p-2 rounded-xl border border-gray-200 hover:bg-gray-50 text-gray-600"
                  title="Refresh stock data"
                >
                  <RefreshCw className={`w-4 h-4 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
                </button>
              </div>
            </div>

            {activeScreen.red_flags && activeScreen.red_flags.length > 0 && (
              <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 flex items-start gap-2.5">
                <AlertTriangle className="w-4 h-4 text-rose-600 flex-shrink-0 mt-0.5" />
                <div className="text-xs text-rose-800">
                  <span className="font-bold">Red Flags Detected: </span>
                  {activeScreen.red_flags.join(' · ')}
                </div>
              </div>
            )}
          </div>

          {/* 2. 10-Pointer / 6-Pointer Checklist */}
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800">
                  {ov.is_bank
                    ? '6-Pointer Banking Fundamental Checklist'
                    : '10-Pointer Fundamental Checklist'}{' '}
                  ({ruleSet.toUpperCase()})
                </h3>
                <p className="text-xs text-gray-500">
                  Passed {activeScreen.passed_count} of {activeScreen.evaluated_count} evaluable criteria (Composite Score: {activeScreen.composite_score}/100)
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(activeScreen.checklist || []).map((item: any) => (
                <div
                  key={item.id}
                  className={`rounded-xl border p-3.5 flex items-start justify-between gap-3 ${
                    item.status === 'pass'
                      ? 'border-emerald-200 bg-emerald-50/30'
                      : item.status === 'fail'
                      ? 'border-rose-200 bg-rose-50/30'
                      : 'border-gray-200 bg-gray-50/60'
                  }`}
                >
                  <div className="flex items-start gap-2.5 min-w-0">
                    {item.status === 'pass' ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0 mt-0.5" />
                    ) : item.status === 'fail' ? (
                      <XCircle className="w-4 h-4 text-rose-600 flex-shrink-0 mt-0.5" />
                    ) : (
                      <MinusCircle className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5" />
                    )}
                    <div className="min-w-0">
                      <div className="text-xs font-bold text-gray-900">{item.label}</div>
                      <div className="text-[11px] text-gray-600 mt-0.5">{item.note}</div>
                    </div>
                  </div>
                  <span
                    className={`text-[11px] font-mono font-semibold px-2 py-0.5 rounded flex-shrink-0 ${
                      item.status === 'pass'
                        ? 'bg-emerald-100 text-emerald-800'
                        : item.status === 'fail'
                        ? 'bg-rose-100 text-rose-800'
                        : 'bg-gray-200 text-gray-600'
                    }`}
                  >
                    {item.status === 'not_applied'
                      ? 'not applied'
                      : item.status === 'no_data'
                      ? 'no data'
                      : item.status.toUpperCase()}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* 3. Ratio Cards Grouped by Category (with formula on hover) + 5Y CAGR Summary */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
              <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800">
                Financial Ratios by Category (Hover for Formula)
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {Object.entries(RATIO_META)
                  .filter(([k]) => (ov.is_bank ? true : !['nim_pct', 'gnpa_pct', 'nnpa_pct', 'casa_pct', 'car_pct'].includes(k)))
                  .map(([key, meta]) => (
                    <div
                      key={key}
                      title={`Formula: ${meta.formula}`}
                      className="rounded-xl border border-gray-200 p-3 hover:border-indigo-300 transition bg-gray-50/40"
                    >
                      <div className="text-[10px] font-semibold uppercase text-indigo-600">
                        {meta.group}
                      </div>
                      <div className="text-xs font-medium text-gray-700 mt-0.5">{meta.label}</div>
                      <div className="text-base font-extrabold text-gray-900 font-mono mt-1">
                        {formatRatioValue(ratios[key], meta.format)}
                      </div>
                      <div className="text-[10px] text-gray-400 mt-0.5 truncate">{meta.formula}</div>
                    </div>
                  ))}
              </div>
            </div>

            {/* 5Y CAGR & CFO/PAT Summary */}
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
              <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800 flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-indigo-600" />
                5-Year Growth & Cash Conversion
              </h3>
              <div className="space-y-2.5 text-xs">
                {[
                  { label: '5Y Revenue CAGR', val: ratios.revenue_cagr_5y },
                  { label: '5Y EBITDA CAGR', val: ratios.ebitda_cagr_5y },
                  { label: '5Y PAT CAGR', val: ratios.pat_cagr_5y },
                  { label: '5Y EPS CAGR', val: ratios.eps_cagr_5y },
                ].map((row) => (
                  <div
                    key={row.label}
                    className="flex items-center justify-between py-2 border-b border-gray-100"
                  >
                    <span className="text-gray-600 font-medium">{row.label}</span>
                    <span className="font-mono font-bold text-gray-900">
                      {formatRatioValue(row.val, 'pct_dec')}
                    </span>
                  </div>
                ))}
                {!ov.is_bank && (
                  <div className="pt-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold text-gray-700">5Y Cumulative CFO / PAT</span>
                      <span className="font-mono font-bold text-emerald-700">
                        {formatRatioValue(ratios.cfo_to_pat, 'x')}
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-500 mt-1">
                      FA Notes Rule: Cumulative Operating Cash Flow should be ≥ 80% of PAT to confirm earnings quality.
                    </p>
                  </div>
                )}
              </div>

              <div className="h-44 pt-2">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={statements}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="pat_cr" name="PAT (₹ Cr)" fill="#4f46e5" radius={[3, 3, 0, 0]} />
                    {!ov.is_bank && (
                      <Bar dataKey="cfo_cr" name="CFO (₹ Cr)" fill="#10b981" radius={[3, 3, 0, 0]} />
                    )}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* 4. Weighted Relative Valuation Card */}
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-bold uppercase tracking-wider text-gray-800 flex items-center gap-2">
                  <Scale className="w-4 h-4 text-indigo-600" />
                  Weighted Relative Valuation vs Sector Medians (FA Course Notes Sec 5)
                </h3>
                <p className="text-xs text-gray-500">{valuation.methodology}</p>
              </div>
              <span className="px-3 py-1 rounded-full text-xs font-bold bg-indigo-50 text-indigo-700 capitalize">
                {(valuation.verdict || '').replace('_', ' ')}
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <div className="rounded-xl border border-gray-200 p-3.5">
                <div className="text-[11px] text-gray-500">Current Market Price</div>
                <div className="text-lg font-extrabold text-gray-900 mt-0.5">
                  ₹{Number(valuation.cmp || 0).toLocaleString('en-IN')}
                </div>
              </div>
              <div className="rounded-xl border border-indigo-200 bg-indigo-50/30 p-3.5">
                <div className="text-[11px] text-indigo-700 font-medium">Weighted Implied Price (Low)</div>
                <div className="text-lg font-extrabold text-indigo-900 mt-0.5">
                  {valuation.fair_value_low != null
                    ? `₹${Number(valuation.fair_value_low).toLocaleString('en-IN')}`
                    : 'N/A'}
                </div>
              </div>
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/30 p-3.5">
                <div className="text-[11px] text-emerald-700 font-medium">Fair Value Upper (+20% Band)</div>
                <div className="text-lg font-extrabold text-emerald-900 mt-0.5">
                  {valuation.fair_value_high != null
                    ? `₹${Number(valuation.fair_value_high).toLocaleString('en-IN')}`
                    : 'N/A'}
                </div>
              </div>
              <div className="rounded-xl border border-gray-200 p-3.5">
                <div className="text-[11px] text-gray-500">Margin of Safety vs Mid</div>
                <div className="text-lg font-extrabold text-gray-900 mt-0.5">
                  {valuation.margin_of_safety_pct != null
                    ? `${Number(valuation.margin_of_safety_pct).toFixed(1)}%`
                    : 'N/A'}
                </div>
              </div>
            </div>
          </div>

          {/* 5. Collapsible Manual Entry for Bank Metrics, FII Holding, and Promoter Pledge */}
          <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden shadow-sm">
            <button
              type="button"
              onClick={() => setShowOverridesSection(!showOverridesSection)}
              className="w-full px-6 py-4 flex items-center justify-between text-left hover:bg-gray-50 transition"
            >
              <div className="flex items-center gap-2">
                <Sliders className="w-4 h-4 text-indigo-600" />
                <span className="text-sm font-bold text-gray-900">
                  Manual Overrides: FII Holding %, Promoter Pledge %, & Bank NPA/CASA/CAR Metrics
                </span>
              </div>
              {showOverridesSection ? (
                <ChevronDown className="w-4 h-4 text-gray-500" />
              ) : (
                <ChevronRight className="w-4 h-4 text-gray-500" />
              )}
            </button>
            {showOverridesSection && (
              <div className="px-6 pb-6 pt-2 border-t border-gray-100 grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    FII Holding % (Target &gt; 5%)
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 18.5"
                    value={fiiInput}
                    onChange={(e) => setFiiInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    Promoter Pledge %
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 0.0"
                    value={pledgeInput}
                    onChange={(e) => setPledgeInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    Bank NIM %
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 4.1"
                    value={nimInput}
                    onChange={(e) => setNimInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    Gross NPA % (≤ 3%)
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 1.25"
                    value={gnpaInput}
                    onChange={(e) => setGnpaInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    Net NPA % (≤ 1%)
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 0.35"
                    value={nnpaInput}
                    onChange={(e) => setNnpaInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    CASA Ratio % (≥ 40%)
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 42.0"
                    value={casaInput}
                    onChange={(e) => setCasaInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-600 mb-1">
                    CAR % (≥ 12%)
                  </label>
                  <input
                    type="number"
                    placeholder="e.g. 18.8"
                    value={carInput}
                    onChange={(e) => setCarInput(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-xs"
                  />
                </div>
              </div>
            )}
          </div>

          {/* 6. Collapsible Economy, Industry & Company (EIC) Context */}
          {eic && (
            <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden shadow-sm">
              <button
                type="button"
                onClick={() => setShowEicSection(!showEicSection)}
                className="w-full px-6 py-4 flex items-center justify-between text-left hover:bg-gray-50 transition"
              >
                <span className="text-sm font-bold text-gray-900">
                  Economy, Industry & Company (EIC) Qualitative Context (Porter&apos;s 5 Forces & 5C)
                </span>
                {showEicSection ? (
                  <ChevronDown className="w-4 h-4 text-gray-500" />
                ) : (
                  <ChevronRight className="w-4 h-4 text-gray-500" />
                )}
              </button>
              {showEicSection && (
                <div className="px-6 pb-6 pt-2 border-t border-gray-100 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                  <div className="space-y-2">
                    <div className="font-bold text-indigo-700 uppercase">Porter&apos;s Five Forces ({ov.sector})</div>
                    {Object.entries(eic.industry?.porters_five_forces || {}).map(([k, v]) => (
                      <div key={k} className="bg-gray-50 rounded-lg p-2.5">
                        <span className="font-semibold capitalize text-gray-800">
                          {k.replace(/_/g, ' ')}:{' '}
                        </span>
                        <span className="text-gray-600">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="space-y-2">
                    <div className="font-bold text-indigo-700 uppercase">Company 5C Credit & Governance Assessment</div>
                    {Object.entries(eic.company_5c || {}).map(([k, v]) => (
                      <div key={k} className="bg-gray-50 rounded-lg p-2.5">
                        <span className="font-semibold capitalize text-gray-800">{k}: </span>
                        <span className="text-gray-600">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 7. Narrative Memo */}
          {data.narrative && (
            <div className="rounded-2xl border border-indigo-200 bg-indigo-50/30 p-6 space-y-2">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-indigo-800">
                <Sparkles className="w-4 h-4 text-indigo-600" />
                Fundamental Research Memo ({ov.symbol})
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
