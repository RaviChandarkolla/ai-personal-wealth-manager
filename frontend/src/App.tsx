import { useState } from 'react'
import axios from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RechartsTooltip, Legend } from 'recharts'
import {
  Send,
  Activity,
  Shield,
  TrendingUp,
  AlertTriangle,
  ExternalLink,
  Database,
  RefreshCw,
  CheckCircle2,
  Sliders,
  BookOpen,
  Sparkles,
  Scale,
  Newspaper,
  FileSearch,
} from 'lucide-react'
import PortfolioUpload from './components/PortfolioUpload'
import FundDetailView from './components/FundDetailView'
import RulesViewer, { type RuleSetId } from './components/RulesViewer'
import FundCompare from './components/FundCompare'
import FundExplorer from './components/FundExplorer'
import StockSyncPanel from './components/StockSyncPanel'
import TrendViewPanel from './components/TrendViewPanel'
import { NotesConfirm, SpecReport } from './components/NotesPanels'
import StockCompare from './components/StockCompare'
import StockAnalyzer from './components/StockAnalyzer'
import Stock360Button, { SavedReports360, Stock360Host } from './components/Stock360Report'
import HistorySidebar from './components/HistorySidebar'
import FollowUpChat, { type FollowUpMessageItem } from './components/FollowUpChat'
import { useAppStore, type RiskPreferenceType, type StockRuleSetId } from './store/useAppStore'

const API_BASE = 'http://localhost:8000/api'

const AMOUNT_PRESETS = [
  { label: '₹50K', value: 50000 },
  { label: '₹1L', value: 100000 },
  { label: '₹5L', value: 500000 },
  { label: '₹10L', value: 1000000 },
  { label: '₹25L', value: 2500000 },
]

const HORIZON_PRESETS = [
  { label: '1Y', value: 1 },
  { label: '3Y', value: 3 },
  { label: '5Y', value: 5 },
  { label: '7Y', value: 7 },
  { label: '10Y', value: 10 },
  { label: '15Y', value: 15 },
]

const RISK_OPTIONS: Array<{
  id: RiskPreferenceType
  label: string
  shortDesc: string
}> = [
  { id: 'capital_preservation', label: 'Capital Preservation', shortDesc: 'Protect principal' },
  { id: 'conservative', label: 'Conservative', shortDesc: 'Low volatility' },
  { id: 'moderate', label: 'Moderate', shortDesc: 'Balanced growth' },
  { id: 'growth', label: 'Growth', shortDesc: 'Long-term wealth' },
  { id: 'aggressive', label: 'Aggressive', shortDesc: 'Max equity upside' },
]

const EXCLUDABLE_CATEGORIES = [
  'Liquid',
  'Short Duration',
  'Corporate Bond',
  'Banking & PSU',
  'Gilt',
  'Arbitrage',
  'Conservative Hybrid',
  'Aggressive Hybrid',
  'Index Fund',
  'Large Cap',
  'Large & Mid Cap',
  'Flexi Cap',
  'Mid Cap',
  'Small Cap',
  'ELSS',
  'Gold ETF',
]

function formatIndianCurrencyHint(amount: number): string {
  if (!amount || amount <= 0) return ''
  if (amount >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Crore`
  if (amount >= 100000) return `₹${(amount / 100000).toFixed(2)} Lakh`
  if (amount >= 1000) return `₹${(amount / 1000).toFixed(1)}K`
  return `₹${amount.toLocaleString('en-IN')}`
}

function parseRationale(rationale: string): {
  proText: string
  watchText: string | null
  inlineCitations: string[]
} {
  const inlineCitations: string[] = []
  const cleaned = rationale
    .replace(/\[([^\[\]]+)\]/g, (_, inner: string) => {
      inner.split(',').forEach((id) => {
        const t = id.trim()
        if (t && !inlineCitations.includes(t)) inlineCitations.push(t)
      })
      return ''
    })
    .replace(/\s+\./g, '.')
    .replace(/\.{2,}/g, '.')
    .trim()

  const watchIdx = cleaned.indexOf('Watch:')
  if (watchIdx !== -1) {
    const proText = cleaned
      .slice(0, watchIdx)
      .replace(/[\s.]+$/, '')
      .trim()
    const watchText = cleaned.slice(watchIdx + 'Watch:'.length).trim()
    return { proText: proText ? `${proText}.` : '', watchText, inlineCitations }
  }
  return { proText: cleaned, watchText: null, inlineCitations }
}

function getFundRoute(): { code: string; runId: string | null; dataMode: string | null } | null {
  const params = new URLSearchParams(window.location.search)
  const queryFund = params.get('fund')
  const runId = params.get('run_id')
  const dataMode = params.get('data_mode')
  if (queryFund) {
    return { code: queryFund, runId, dataMode }
  }
  const match = window.location.pathname.match(/^\/fund\/([^/]+)/)
  if (match) {
    return { code: decodeURIComponent(match[1]), runId, dataMode }
  }
  return null
}

function getCompareRoute(): { isCompare: boolean; codeA: string | null; codeB: string | null } {
  const params = new URLSearchParams(window.location.search)
  const codeA = params.get('a')
  const codeB = params.get('b')
  const isCompare =
    (window.location.pathname.startsWith('/compare') &&
      !window.location.pathname.startsWith('/compare-stocks')) ||
    Boolean(codeA && codeB)
  return { isCompare, codeA, codeB }
}

function getExploreRoute(): { isExplore: boolean; code: string | null } {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const isExplore = window.location.pathname.startsWith('/explore')
  return { isExplore, code }
}

function getCompareStocksRoute(): { isCompareStocks: boolean } {
  return { isCompareStocks: window.location.pathname.startsWith('/compare-stocks') }
}

function getAnalyzeStockRoute(): { isAnalyzeStock: boolean; symbol: string | null } {
  const params = new URLSearchParams(window.location.search)
  const symbol = params.get('symbol')
  return {
    isAnalyzeStock: window.location.pathname.startsWith('/analyze-stock'),
    symbol,
  }
}

type AppView = 'advisor' | 'compare' | 'explore' | 'compare-stocks' | 'analyze-stock'

function App() {
  const fundRoute = getFundRoute()
  const compareRoute = getCompareRoute()
  const exploreRoute = getExploreRoute()
  const compareStocksRoute = getCompareStocksRoute()
  const analyzeStockRoute = getAnalyzeStockRoute()
  const queryClient = useQueryClient()

  const [activeView, setActiveView] = useState<AppView>(
    analyzeStockRoute.isAnalyzeStock
      ? 'analyze-stock'
      : compareStocksRoute.isCompareStocks
      ? 'compare-stocks'
      : exploreRoute.isExplore
      ? 'explore'
      : compareRoute.isCompare
      ? 'compare'
      : 'advisor'
  )

  const switchView = (view: AppView) => {
    setActiveView(view)
    if (view === 'compare') {
      if (
        !window.location.pathname.startsWith('/compare') ||
        window.location.pathname.startsWith('/compare-stocks')
      ) {
        window.history.pushState({}, '', '/compare')
      }
    } else if (view === 'explore') {
      if (!window.location.pathname.startsWith('/explore')) {
        window.history.pushState({}, '', '/explore')
      }
    } else if (view === 'compare-stocks') {
      if (!window.location.pathname.startsWith('/compare-stocks')) {
        window.history.pushState({}, '', '/compare-stocks')
      }
    } else if (view === 'analyze-stock') {
      if (!window.location.pathname.startsWith('/analyze-stock')) {
        window.history.pushState({}, '', '/analyze-stock')
      }
    } else {
      if (
        window.location.pathname.startsWith('/compare') ||
        window.location.pathname.startsWith('/explore') ||
        window.location.pathname.startsWith('/compare-stocks') ||
        window.location.pathname.startsWith('/analyze-stock')
      ) {
        window.history.pushState({}, '', '/')
      }
    }
  }

  const inputTab = useAppStore((state) => state.inputTab)
  const setInputTab = useAppStore((state) => state.setInputTab)
  const amount = useAppStore((state) => state.amount)
  const setAmount = useAppStore((state) => state.setAmount)
  const horizonYears = useAppStore((state) => state.horizonYears)
  const setHorizonYears = useAppStore((state) => state.setHorizonYears)
  const riskPreference = useAppStore((state) => state.riskPreference)
  const setRiskPreference = useAppStore((state) => state.setRiskPreference)
  const purpose = useAppStore((state) => state.purpose)
  const setPurpose = useAppStore((state) => state.setPurpose)
  const excludedCategories = useAppStore((state) => state.excludedCategories)
  const setExcludedCategories = useAppStore((state) => state.setExcludedCategories)
  const toggleExcludedCategory = useAppStore((state) => state.toggleExcludedCategory)
  const emergencyMonths = useAppStore((state) => state.emergencyMonths)
  const setEmergencyMonths = useAppStore((state) => state.setEmergencyMonths)
  const savingsRate = useAppStore((state) => state.savingsRate)
  const setSavingsRate = useAppStore((state) => state.setSavingsRate)
  const incomeStability = useAppStore((state) => state.incomeStability)
  const setIncomeStability = useAppStore((state) => state.setIncomeStability)
  const currentGoalQuery = useAppStore((state) => state.currentGoalQuery)
  const setGoalQuery = useAppStore((state) => state.setGoalQuery)
  const activeRunId = useAppStore((state) => state.activeRunId)
  const setActiveRunId = useAppStore((state) => state.setActiveRunId)

  const includeStocks = useAppStore((state) => state.includeStocks)
  const setIncludeStocks = useAppStore((state) => state.setIncludeStocks)
  const stockRuleSet = useAppStore((state) => state.stockRuleSet)
  const setStockRuleSet = useAppStore((state) => state.setStockRuleSet)
  const trendBasedStocks = useAppStore((state) => state.trendBasedStocks)
  const setTrendBasedStocks = useAppStore((state) => state.setTrendBasedStocks)
  const stock360Veto = useAppStore((state) => state.stock360Veto)
  const setStock360Veto = useAppStore((state) => state.setStock360Veto)
  const stock360MinSellSignals = useAppStore((state) => state.stock360MinSellSignals)
  const setStock360MinSellSignals = useAppStore((state) => state.setStock360MinSellSignals)
  const minMarketCapCr = useAppStore((state) => state.minMarketCapCr)
  const setMinMarketCapCr = useAppStore((state) => state.setMinMarketCapCr)
  const minTradedValueCr = useAppStore((state) => state.minTradedValueCr)
  const setMinTradedValueCr = useAppStore((state) => state.setMinTradedValueCr)
  const setSelectedAnalyzeSymbol = useAppStore((state) => state.setSelectedAnalyzeSymbol)

  const [isRulesViewerOpen, setIsRulesViewerOpen] = useState(false)
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [showProvenanceDetails, setShowProvenanceDetails] = useState(false)
  const [showAdvancedConstraints, setShowAdvancedConstraints] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [pendingConfirm, setPendingConfirm] = useState<{
    payload: Record<string, any>
    preview: { understood: string[]; warnings: string[]; llm_calls_budget: number; portfolio_spec: any }
  } | null>(null)

  const modeQuery = useQuery({
    queryKey: ['dataMode'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/config/data-mode`)
      return res.data as {
        data_mode: 'synthetic' | 'real'
        default_data_mode: 'synthetic' | 'real'
        real_data_provider: string
        available_providers: string[]
        db_path: string
        fund_count: number
        latest_nav_date: string | null
      }
    },
  })

  const ruleSetQuery = useQuery({
    queryKey: ['ruleSetConfig'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/config/rule-set`)
      return res.data as {
        active: RuleSetId
        default: RuleSetId
        available: Array<{
          id: RuleSetId
          label: string
          description: string
          highlights: string[]
        }>
      }
    },
  })

  const switchModeMutation = useMutation({
    mutationFn: async (newMode: 'synthetic' | 'real') => {
      const res = await axios.post(`${API_BASE}/config/data-mode`, {
        data_mode: newMode,
        auto_seed: true,
        persist_env: true,
      })
      return res.data
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['dataMode'], data)
    },
  })

  const switchRuleSetMutation = useMutation({
    mutationFn: async (newRuleSet: RuleSetId) => {
      const res = await axios.post(`${API_BASE}/config/rule-set`, {
        rule_set: newRuleSet,
        persist_env: true,
      })
      return res.data
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['ruleSetConfig'], data)
    },
  })

  const syncDataMutation = useMutation({
    mutationFn: async () => {
      const activeMode = modeQuery.data?.data_mode || 'synthetic'
      const res = await axios.post(`${API_BASE}/data/sync`, {
        data_mode: activeMode,
        refresh_amfi: activeMode === 'real',
        refresh_master: activeMode === 'real',
      })
      return res.data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dataMode'] })
      queryClient.invalidateQueries({ queryKey: ['compareSchemes'] })
      queryClient.invalidateQueries({ queryKey: ['compareCategories'] })
    },
  })

  const activeRuleSet: RuleSetId = ruleSetQuery.data?.active || 'set1'

  const adviseMutation = useMutation({
    mutationFn: async (payload: {
      query?: string
      amount?: number
      horizon_years?: number
      risk_preference?: RiskPreferenceType
      purpose?: string
      excluded_categories?: string[]
      emergency_months?: number | null
      savings_rate?: number | null
      income_stability?: 'stable' | 'variable' | null
      portfolio_spec?: any
    }) => {
      const activeMode = modeQuery.data?.data_mode
      const res = await axios.post(`${API_BASE}/advise`, {
        ...payload,
        assume_yes: true,
        data_mode: activeMode,
        rule_set: activeRuleSet,
        include_stocks: includeStocks,
        stock_rule_set: stockRuleSet,
        trend_based_stocks: trendBasedStocks,
        stock360_veto: includeStocks ? stock360Veto : undefined,
        stock360_min_sell_signals: includeStocks && stock360Veto ? stock360MinSellSignals : undefined,
        min_market_cap_cr: minMarketCapCr ?? undefined,
        min_traded_value_cr: minTradedValueCr ?? undefined,
      })
      return res.data
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['dataMode'] })
      queryClient.invalidateQueries({ queryKey: ['history'] })
      if (data?.run_id) {
        setActiveRunId(data.run_id)
      }
    },
  })

  const historyItemQuery = useQuery({
    queryKey: ['history', activeRunId],
    enabled: Boolean(activeRunId),
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/history/${encodeURIComponent(activeRunId!)}`)
      return res.data as {
        run_id: string
        response: any
        messages: FollowUpMessageItem[]
        reports360?: { report_id: string; symbol: string; status: string; created_at: string }[]
      }
    },
  })

  const handleNewAdvice = () => {
    setActiveRunId(null)
    adviseMutation.reset()
  }

  if (fundRoute) {
    return (
      <FundDetailView
        code={fundRoute.code}
        runId={fundRoute.runId}
        dataMode={fundRoute.dataMode}
      />
    )
  }

  const canSubmitHybrid = amount > 0 && horizonYears > 0 && Boolean(riskPreference)
  const canSubmitSentence = Boolean(currentGoalQuery.trim())

  const handleAdvise = async (e: React.FormEvent) => {
    e.preventDefault()
    setPendingConfirm(null)
    let payload: Record<string, any>
    if (inputTab === 'sentence') {
      if (!canSubmitSentence) return
      payload = { query: currentGoalQuery.trim() }
    } else {
      if (!canSubmitHybrid) return
      payload = {
        query: currentGoalQuery.trim() || undefined,
        amount,
        horizon_years: horizonYears,
        risk_preference: riskPreference,
        purpose: purpose.trim() || undefined,
        excluded_categories: excludedCategories.length > 0 ? excludedCategories : undefined,
        emergency_months: emergencyMonths ?? undefined,
        savings_rate: savingsRate ?? undefined,
        income_stability: incomeStability ?? undefined,
      }
    }
    if (payload.query) {
      // Show what the notes asked for (counts of funds/stocks) before spending an advice run.
      setPreviewing(true)
      try {
        const res = await axios.post(`${API_BASE}/advise/preview`, {
          ...payload,
          data_mode: modeQuery.data?.data_mode,
        })
        if (res.data?.custom) {
          setPendingConfirm({ payload, preview: res.data })
          return
        }
      } catch {
        // Fall through: /advise reports the real error (missing info, guardrail, provider).
      } finally {
        setPreviewing(false)
      }
    }
    adviseMutation.mutate(payload)
  }

  const confirmNotes = () => {
    if (!pendingConfirm) return
    adviseMutation.mutate({
      ...pendingConfirm.payload,
      portfolio_spec: pendingConfirm.preview.portfolio_spec,
    })
    setPendingConfirm(null)
  }

  const { data: mutationResult, isPending: advising, error: mutationError } = adviseMutation
  const loading = advising || previewing
  const result = activeRunId
    ? historyItemQuery.data?.response ??
      (mutationResult?.run_id === activeRunId ? mutationResult : null)
    : mutationResult
  const historyMessages: FollowUpMessageItem[] = historyItemQuery.data?.messages || []
  const error = mutationError
    ? (mutationError as any).response?.data?.detail ||
      mutationError.message ||
      'An error occurred'
    : null

  const activeMode = modeQuery.data?.data_mode || 'synthetic'
  const isModeSwitching = switchModeMutation.isPending || syncDataMutation.isPending

  const COLORS = ['#4f46e5', '#10b981', '#f59e0b', '#ef4444', '#a855f7']

  const stockSleeveWeight = Number(result?.stock_sleeve_weight ?? 0)
  const allocationData = result
    ? (() => {
        const entries = Object.entries(result.target)
          .filter(([_, weight]) => (weight as number) > 0)
          .map(([name, value]) => ({ name: name.toUpperCase(), value: Number(value) }))
        if (stockSleeveWeight > 0) {
          const eqIdx = entries.findIndex((e) => e.name === 'EQUITY')
          if (eqIdx !== -1 && entries[eqIdx].value > stockSleeveWeight) {
            entries[eqIdx] = {
              name: 'EQUITY (MF)',
              value: Number((entries[eqIdx].value - stockSleeveWeight).toFixed(2)),
            }
            entries.splice(eqIdx + 1, 0, {
              name: 'DIRECT EQUITY (STOCKS)',
              value: Number(stockSleeveWeight.toFixed(2)),
            })
          }
        }
        return entries
      })()
    : []

  const openAnalyzeStock = (symbol: string) => {
    setSelectedAnalyzeSymbol(symbol)
    setActiveView('analyze-stock')
    window.history.pushState({}, '', `/analyze-stock?symbol=${encodeURIComponent(symbol)}`)
  }

  const goalAmount = result?.goal?.amount ?? result?.assumptions?.amount ?? 0
  const goalHorizon = result?.goal?.horizon_years ?? result?.assumptions?.horizon_years ?? '-'
  const totalAllocatedAmount = result?.recommendations
    ? result.recommendations.reduce((acc: number, r: any) => acc + (r.amount || 0), 0)
    : 0

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 font-sans p-4 md:p-8">
      <div className="max-w-7xl mx-auto space-y-8">
        <header className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-6">
          <div className="flex items-center space-x-3">
            <TrendingUp className="w-8 h-8 text-indigo-600" />
            <div>
              <h1 className="text-3xl font-bold text-gray-800">AI Personal Wealth Advisor</h1>
              <p className="text-xs text-gray-500">
                Audited multi-agent mutual fund & fundamental stock research, suitability guardrails & grounded citations
              </p>
            </div>
          </div>

          {/* Data Mode Switcher & Rule Set Switcher */}
          <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-3">
            {/* Data Source */}
            <div className="bg-white px-4 py-2.5 rounded-xl shadow-xs border border-gray-200 flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-600">
                <Database className="w-4 h-4 text-indigo-600" />
                <span>Data:</span>
              </div>

              <div className="inline-flex rounded-lg bg-gray-100 p-1">
                <button
                  type="button"
                  disabled={isModeSwitching || loading}
                  onClick={() => activeMode !== 'synthetic' && switchModeMutation.mutate('synthetic')}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition ${
                    activeMode === 'synthetic'
                      ? 'bg-white text-indigo-700 shadow-xs'
                      : 'text-gray-600 hover:text-gray-900'
                  } disabled:opacity-50`}
                >
                  Synthetic
                </button>
                <button
                  type="button"
                  disabled={isModeSwitching || loading}
                  onClick={() => activeMode !== 'real' && switchModeMutation.mutate('real')}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition ${
                    activeMode === 'real'
                      ? 'bg-emerald-600 text-white shadow-xs'
                      : 'text-gray-600 hover:text-gray-900'
                  } disabled:opacity-50`}
                >
                  Real
                </button>
              </div>

              <button
                type="button"
                disabled={isModeSwitching || loading}
                onClick={() => syncDataMutation.mutate()}
                title={
                  activeMode === 'real'
                    ? 'Fetch latest historical NAVs from mfapi.in + AMFI NAVAll.txt'
                    : 'Re-seed synthetic demo universe'
                }
                className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border border-gray-200 text-xs font-medium text-gray-700 hover:bg-gray-50 transition disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isModeSwitching ? 'animate-spin text-indigo-600' : ''}`} />
                <span>{isModeSwitching ? 'Syncing...' : 'Sync MF'}</span>
              </button>

              {modeQuery.data && (
                <div className="hidden xl:flex items-center gap-1.5 text-[11px] text-gray-500 border-l border-gray-200 pl-2.5">
                  <CheckCircle2 className={`w-3.5 h-3.5 ${activeMode === 'real' ? 'text-emerald-600' : 'text-indigo-500'}`} />
                  <span>
                    <strong>{modeQuery.data.fund_count}</strong> funds
                  </span>
                </div>
              )}

              <div className="border-l border-gray-200 pl-2.5">
                <StockSyncPanel compact />
              </div>
            </div>

            {/* Rule Set Switcher & Viewer Button */}
            <div className="bg-white px-4 py-2.5 rounded-xl shadow-xs border border-gray-200 flex flex-wrap items-center gap-2.5">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-600">
                <Sliders className="w-4 h-4 text-indigo-600" />
                <span>MF Rules:</span>
              </div>

              <div className="inline-flex rounded-lg bg-gray-100 p-1">
                <button
                  type="button"
                  disabled={loading}
                  title="Conservative baseline selection rules and SEBI standard bands"
                  onClick={() => activeRuleSet !== 'set1' && switchRuleSetMutation.mutate('set1')}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition ${
                    activeRuleSet === 'set1'
                      ? 'bg-white text-indigo-700 shadow-xs'
                      : 'text-gray-600 hover:text-gray-900'
                  } disabled:opacity-50`}
                >
                  Set 1 · Default
                </button>
                <button
                  type="button"
                  disabled={loading}
                  title="Goal-aligned aggressive hybrid allocation, 0% equity <3y, positive alpha, trusted AMC bonus"
                  onClick={() => activeRuleSet !== 'set2' && switchRuleSetMutation.mutate('set2')}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition ${
                    activeRuleSet === 'set2'
                      ? 'bg-purple-600 text-white shadow-xs'
                      : 'text-gray-600 hover:text-gray-900'
                  } disabled:opacity-50`}
                >
                  Set 2 · Magic MF
                </button>
                <button
                  type="button"
                  disabled={loading}
                  title="Strictest filters, blended weights, overlapping allocation ranges"
                  onClick={() => activeRuleSet !== 'combined' && switchRuleSetMutation.mutate('combined')}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition ${
                    activeRuleSet === 'combined'
                      ? 'bg-emerald-600 text-white shadow-xs'
                      : 'text-gray-600 hover:text-gray-900'
                  } disabled:opacity-50`}
                >
                  Combined · 1 + 2
                </button>
              </div>

              <button
                type="button"
                onClick={() => setIsRulesViewerOpen(true)}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-xs font-medium text-gray-700 shadow-2xs transition"
                title="View interactive rules catalog, live examples & comparison table"
              >
                <BookOpen className="w-3.5 h-3.5 text-indigo-600" />
                <span>View Rules</span>
              </button>
            </div>
          </div>
        </header>

        {/* Top-Level Navigation: Advisor | Compare Funds | Fund Explorer | Compare Stocks | Analyse Stock */}
        <nav className="flex flex-wrap items-center gap-2 border-b border-gray-200 pb-3">
          <button
            type="button"
            onClick={() => switchView('advisor')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition ${
              activeView === 'advisor'
                ? 'bg-indigo-600 text-white shadow-xs'
                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            <TrendingUp className="w-4 h-4" />
            <span>Advisor</span>
          </button>
          <button
            type="button"
            onClick={() => switchView('compare')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition ${
              activeView === 'compare'
                ? 'bg-indigo-600 text-white shadow-xs'
                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            <Scale className="w-4 h-4" />
            <span>Compare Funds</span>
          </button>
          <button
            type="button"
            onClick={() => switchView('explore')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition ${
              activeView === 'explore'
                ? 'bg-indigo-600 text-white shadow-xs'
                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            <FileSearch className="w-4 h-4" />
            <span>Fund Explorer</span>
          </button>
          <button
            type="button"
            onClick={() => switchView('compare-stocks')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition ${
              activeView === 'compare-stocks'
                ? 'bg-indigo-600 text-white shadow-xs'
                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            <Scale className="w-4 h-4" />
            <span>Compare Stocks</span>
          </button>
          <button
            type="button"
            onClick={() => switchView('analyze-stock')}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition ${
              activeView === 'analyze-stock'
                ? 'bg-indigo-600 text-white shadow-xs'
                : 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50'
            }`}
          >
            <Activity className="w-4 h-4" />
            <span>Analyse Stock</span>
          </button>
        </nav>

        {activeView === 'compare' ? (
          <FundCompare
            dataMode={activeMode}
            initialCodeA={compareRoute.codeA}
            initialCodeB={compareRoute.codeB}
          />
        ) : activeView === 'explore' ? (
          <FundExplorer
            dataMode={activeMode}
            initialCode={exploreRoute.code}
          />
        ) : activeView === 'compare-stocks' ? (
          <StockCompare onAnalyzeStock={openAnalyzeStock} />
        ) : activeView === 'analyze-stock' ? (
          <StockAnalyzer />
        ) : (
          <div className="flex flex-col lg:flex-row gap-6 items-start">
            <HistorySidebar onNewAdvice={handleNewAdvice} />
            <div className="flex-1 min-w-0 space-y-6 w-full">
            {/* Hybrid Goal Input Section */}
            <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
            <div>
              <h2 className="text-xl font-semibold text-gray-800">What is your financial goal?</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Select mandatory parameters directly for instant deterministic profiling, or add natural-language context & exclusions.
              </p>
            </div>

            {/* Mode Switcher */}
            <div className="inline-flex rounded-lg bg-gray-100 p-1 self-start sm:self-auto">
              <button
                type="button"
                disabled={loading}
                onClick={() => setInputTab('hybrid')}
                className={`px-3 py-1.5 rounded-md text-xs font-semibold transition ${
                  inputTab === 'hybrid'
                    ? 'bg-white text-indigo-700 shadow-xs'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Structured + AI Notes (Hybrid)
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={() => setInputTab('sentence')}
                className={`px-3 py-1.5 rounded-md text-xs font-semibold transition ${
                  inputTab === 'sentence'
                    ? 'bg-white text-indigo-700 shadow-xs'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                Natural-Language Sentence
              </button>
            </div>
          </div>

          <form onSubmit={handleAdvise} className="space-y-5">
            {inputTab === 'hybrid' ? (
              <>
                {/* 1. Mandatory Core Parameters: Amount, Horizon, Risk Preference */}
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
                  {/* Investment Amount */}
                  <div className="lg:col-span-4 bg-gray-50/70 p-4 rounded-xl border border-gray-200/80 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-semibold uppercase tracking-wider text-gray-600">
                        Investment Amount (₹) *
                      </label>
                      <span className="text-xs font-semibold text-indigo-600">
                        {formatIndianCurrencyHint(amount)}
                      </span>
                    </div>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 font-semibold text-sm">
                        ₹
                      </span>
                      <input
                        type="number"
                        min={500}
                        step={500}
                        value={amount || ''}
                        onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
                        disabled={loading}
                        className="w-full pl-7 pr-3 py-2 bg-white rounded-lg border border-gray-300 text-sm font-semibold text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        placeholder="500000"
                      />
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {AMOUNT_PRESETS.map((preset) => (
                        <button
                          key={preset.value}
                          type="button"
                          disabled={loading}
                          onClick={() => setAmount(preset.value)}
                          className={`px-2.5 py-1 rounded-md text-xs font-medium border transition ${
                            amount === preset.value
                              ? 'bg-indigo-600 text-white border-indigo-600'
                              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-100'
                          }`}
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Investment Horizon */}
                  <div className="lg:col-span-3 bg-gray-50/70 p-4 rounded-xl border border-gray-200/80 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-semibold uppercase tracking-wider text-gray-600">
                        Horizon (Years) *
                      </label>
                      <span className="text-xs font-semibold text-indigo-600">
                        {horizonYears >= 1
                          ? `${horizonYears} ${horizonYears === 1 ? 'Year' : 'Years'}`
                          : `${Math.round(horizonYears * 12)} Months`}
                      </span>
                    </div>
                    <input
                      type="number"
                      min={0.5}
                      max={40}
                      step={0.5}
                      value={horizonYears || ''}
                      onChange={(e) => setHorizonYears(Math.max(0.5, Number(e.target.value)))}
                      disabled={loading}
                      className="w-full px-3 py-2 bg-white rounded-lg border border-gray-300 text-sm font-semibold text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="5"
                    />
                    <div className="flex flex-wrap gap-1.5">
                      {HORIZON_PRESETS.map((preset) => (
                        <button
                          key={preset.value}
                          type="button"
                          disabled={loading}
                          onClick={() => setHorizonYears(preset.value)}
                          className={`px-2.5 py-1 rounded-md text-xs font-medium border transition ${
                            horizonYears === preset.value
                              ? 'bg-indigo-600 text-white border-indigo-600'
                              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-100'
                          }`}
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Risk Appetite */}
                  <div className="lg:col-span-5 bg-gray-50/70 p-4 rounded-xl border border-gray-200/80 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-semibold uppercase tracking-wider text-gray-600">
                        Risk Preference *
                      </label>
                      <span className="text-[11px] text-gray-500">
                        Capped by horizon & emergency buffer
                      </span>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                      {RISK_OPTIONS.map((opt) => {
                        const active = riskPreference === opt.id
                        return (
                          <button
                            key={opt.id}
                            type="button"
                            disabled={loading}
                            onClick={() => setRiskPreference(opt.id)}
                            className={`text-left p-2 rounded-lg border transition ${
                              active
                                ? 'bg-indigo-600 text-white border-indigo-600 shadow-2xs'
                                : 'bg-white text-gray-800 border-gray-200 hover:bg-gray-100'
                            }`}
                          >
                            <div className="text-xs font-semibold leading-tight">{opt.label}</div>
                            <div
                              className={`text-[10px] mt-0.5 ${
                                active ? 'text-indigo-100' : 'text-gray-400'
                              }`}
                            >
                              {opt.shortDesc}
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                </div>

                {/* 2. Optional Natural-Language Context & Advanced Constraints */}
                <div className="space-y-3">
                  <div className="flex flex-col md:flex-row gap-3">
                    <div className="flex-1">
                      <input
                        type="text"
                        value={currentGoalQuery}
                        onChange={(e) => setGoalQuery(e.target.value)}
                        placeholder="Optional AI notes: e.g. 'Saving for daughter's college, avoid Small Cap and ELSS'"
                        className="w-full px-4 py-2.5 text-sm rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        disabled={loading}
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => setShowAdvancedConstraints((prev) => !prev)}
                      className="px-3.5 py-2.5 rounded-lg border border-gray-200 bg-gray-50 hover:bg-gray-100 text-xs font-semibold text-gray-700 transition whitespace-nowrap"
                    >
                      {showAdvancedConstraints
                        ? 'Hide Exclusions & Capacity'
                        : `Exclusions & Capacity${
                            excludedCategories.length > 0 ? ` (${excludedCategories.length})` : ''
                          }`}
                    </button>
                    <button
                      type="submit"
                      disabled={loading || !canSubmitHybrid}
                      className="bg-indigo-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-indigo-700 transition disabled:opacity-50 flex items-center justify-center gap-2 whitespace-nowrap"
                    >
                      {loading ? (
                        'Processing...'
                      ) : (
                        <>
                          <Send className="w-4 h-4" />
                          Advise Me
                        </>
                      )}
                    </button>
                  </div>

                  {/* Execution Path Indicator */}
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
                    <div className="flex items-center gap-2">
                      {currentGoalQuery.trim() ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-purple-50 text-purple-700 border border-purple-200 font-medium">
                          <Sparkles className="w-3 h-3" />
                          Hybrid Mode: Structured Core + AI Extraction for Notes & Exclusions
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium">
                          <CheckCircle2 className="w-3 h-3" />
                          Direct Structured Mode: Instant deterministic goal (bypasses LLM profiling)
                        </span>
                      )}
                    </div>
                    {(purpose || excludedCategories.length > 0) && (
                      <div className="text-[11px] text-gray-500">
                        {purpose && <span className="mr-3">Purpose: <strong>{purpose}</strong></span>}
                        {excludedCategories.length > 0 && (
                          <span>
                            Excluded: <strong>{excludedCategories.join(', ')}</strong>
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Collapsible Structured Exclusions, Purpose & Risk Capacity Overrides */}
                  {showAdvancedConstraints && (
                    <div className="p-4 rounded-xl bg-gray-50 border border-gray-200 space-y-4">
                      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div>
                          <label className="block text-xs font-semibold text-gray-600 mb-1">
                            Goal Purpose (Optional)
                          </label>
                          <input
                            type="text"
                            value={purpose}
                            onChange={(e) => setPurpose(e.target.value)}
                            placeholder="e.g. Retirement, House down payment"
                            className="w-full px-3 py-1.5 bg-white rounded-lg border border-gray-300 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-semibold text-gray-600 mb-1">
                            Emergency Buffer (Months)
                          </label>
                          <input
                            type="number"
                            min={0}
                            max={36}
                            step={1}
                            value={emergencyMonths ?? ''}
                            onChange={(e) =>
                              setEmergencyMonths(e.target.value === '' ? null : Number(e.target.value))
                            }
                            placeholder="Default: 6 (<3 caps equity at 30%)"
                            className="w-full px-3 py-1.5 bg-white rounded-lg border border-gray-300 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-semibold text-gray-600 mb-1">
                            Savings Rate (0.0 – 1.0)
                          </label>
                          <input
                            type="number"
                            min={0}
                            max={1}
                            step={0.05}
                            value={savingsRate ?? ''}
                            onChange={(e) =>
                              setSavingsRate(e.target.value === '' ? null : Number(e.target.value))
                            }
                            placeholder="Default: 0.20 (20%)"
                            className="w-full px-3 py-1.5 bg-white rounded-lg border border-gray-300 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-semibold text-gray-600 mb-1">
                            Income Stability
                          </label>
                          <select
                            value={incomeStability ?? ''}
                            onChange={(e) =>
                              setIncomeStability(
                                e.target.value === '' ? null : (e.target.value as 'stable' | 'variable')
                              )
                            }
                            className="w-full px-3 py-1.5 bg-white rounded-lg border border-gray-300 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          >
                            <option value="">Default (Stable / Salaried)</option>
                            <option value="stable">Stable (Salaried)</option>
                            <option value="variable">Variable (Business / Freelance)</option>
                          </select>
                        </div>
                      </div>

                      <div>
                        <div className="flex items-center justify-between mb-1.5">
                          <label className="text-xs font-semibold text-gray-600">
                            Exclude Specific Fund Categories
                          </label>
                          {excludedCategories.length > 0 && (
                            <button
                              type="button"
                              onClick={() => setExcludedCategories([])}
                              className="text-[11px] text-indigo-600 hover:underline"
                            >
                              Clear exclusions
                            </button>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {EXCLUDABLE_CATEGORIES.map((cat) => {
                            const isExcluded = excludedCategories.includes(cat)
                            return (
                              <button
                                key={cat}
                                type="button"
                                onClick={() => toggleExcludedCategory(cat)}
                                className={`px-2.5 py-1 rounded-md text-xs font-medium border transition ${
                                  isExcluded
                                    ? 'bg-red-50 text-red-700 border-red-300 line-through'
                                    : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-100'
                                }`}
                              >
                                {cat}
                              </button>
                            )
                          })}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Direct Equity (Stocks) Sleeve Controls */}
                  <div className="p-4 rounded-xl bg-amber-50/40 border border-amber-200/80 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={includeStocks}
                          onChange={(e) => setIncludeStocks(e.target.checked)}
                          disabled={loading}
                          className="rounded text-indigo-600 focus:ring-indigo-500 h-4 w-4"
                        />
                        <span className="text-xs font-bold text-gray-800">
                          Include direct stocks (Direct Equity sleeve)
                        </span>
                      </label>
                      <span className="text-[11px] text-gray-500">
                        Carves up to 20% (Moderate) / 30% (Aggressive) of Equity into screened stocks (Horizon ≥ 5Y)
                      </span>
                    </div>

                    {includeStocks && (
                      <div className="grid grid-cols-1 md:grid-cols-12 gap-3 pt-2 border-t border-amber-200/60 items-end">
                        <div className="md:col-span-6">
                          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
                            Stock Screening Rule Set (Independent of MF Rules)
                          </label>
                          <div className="grid grid-cols-2 gap-1.5 bg-white p-1 rounded-lg border border-gray-200">
                            {(
                              [
                                {
                                  id: 'set2',
                                  label: 'Set 2 · FA Course Notes (10-Pt / 6-Pt)',
                                },
                                {
                                  id: 'set1',
                                  label: 'Set 1 · Classic Equity Heuristics',
                                },
                              ] as Array<{ id: StockRuleSetId; label: string }>
                            ).map((opt) => (
                              <button
                                key={opt.id}
                                type="button"
                                onClick={() => setStockRuleSet(opt.id)}
                                className={`px-2.5 py-1.5 rounded-md text-[11px] font-semibold transition ${
                                  stockRuleSet === opt.id
                                    ? 'bg-indigo-600 text-white shadow-2xs'
                                    : 'text-gray-600 hover:bg-gray-50'
                                }`}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="md:col-span-3">
                          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
                            Min Market Cap (₹ Cr, optional)
                          </label>
                          <input
                            type="number"
                            min={0}
                            step={500}
                            value={minMarketCapCr ?? ''}
                            onChange={(e) =>
                              setMinMarketCapCr(
                                e.target.value === '' ? null : Math.max(0, Number(e.target.value))
                              )
                            }
                            placeholder="No limit (default)"
                            className="w-full px-2.5 py-1.5 bg-white rounded-lg border border-gray-300 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>

                        <div className="md:col-span-3">
                          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
                            Min Daily Traded Value (₹ Cr, optional)
                          </label>
                          <input
                            type="number"
                            min={0}
                            step={5}
                            value={minTradedValueCr ?? ''}
                            onChange={(e) =>
                              setMinTradedValueCr(
                                e.target.value === '' ? null : Math.max(0, Number(e.target.value))
                              )
                            }
                            placeholder="No limit (default)"
                            className="w-full px-2.5 py-1.5 bg-white rounded-lg border border-gray-300 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          />
                        </div>
                      </div>
                    )}

                    {includeStocks && (
                      <label className="flex items-start gap-2 pt-2 border-t border-amber-200/60 cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={trendBasedStocks}
                          onChange={(e) => setTrendBasedStocks(e.target.checked)}
                          disabled={loading}
                          className="mt-0.5 rounded text-indigo-600 focus:ring-indigo-500 h-4 w-4"
                        />
                        <span className="text-xs text-gray-800">
                          <span className="font-bold">Trend-based stock selection</span>
                          <span className="block text-[11px] text-gray-500">
                            Blends 3/6/12-month momentum into stock scores, tilts position sizes toward
                            stocks in an uptrend, and shifts the stock sleeve by up to ±5 pts of Equity
                            based on market breadth. Uncheck for fundamentals-only rules.
                          </span>
                        </span>
                      </label>
                    )}
                    {includeStocks && (
                      <div className="pt-2 border-t border-amber-200/60 space-y-1">
                        <label className="flex items-start gap-2 cursor-pointer select-none">
                          <input
                            type="checkbox"
                            checked={stock360Veto}
                            onChange={(e) => setStock360Veto(e.target.checked)}
                            disabled={loading}
                            className="mt-0.5 rounded text-indigo-600 focus:ring-indigo-500 h-4 w-4"
                          />
                          <span className="text-xs text-gray-800">
                            <span className="font-bold">Exclude stocks that fail the 360° quant check</span>
                            <span className="block text-[11px] text-gray-500">
                              Off by default. Drops a pick when its cached data gives a SELL/AVOID verdict or enough
                              legend sell signals; the weight goes back to funds. Uses numbers only (no AI text).
                            </span>
                          </span>
                        </label>
                        {stock360Veto && (
                          <label className="flex items-center gap-2 pl-6 text-[11px] text-gray-600">
                            Min sell signals to drop
                            <input
                              type="number" min={1} max={10} value={stock360MinSellSignals}
                              onChange={(e) => setStock360MinSellSignals(Math.max(1, Math.min(10, Number(e.target.value) || 5)))}
                              className="w-14 rounded border border-gray-300 px-1 py-0.5"
                            />
                          </label>
                        )}
                      </div>
                    )}
                    <TrendViewPanel disabled={loading} />
                  </div>
                </div>
              </>
            ) : (
              /* Sentence-Only Mode */
              <div className="space-y-2">
                <div className="flex gap-3">
                  <input
                    type="text"
                    value={currentGoalQuery}
                    onChange={(e) => setGoalQuery(e.target.value)}
                    placeholder="e.g. I have ₹50,000 to invest for 5 years with moderate risk, avoid mid cap"
                    className="flex-1 px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    disabled={loading}
                  />
                  <button
                    type="submit"
                    disabled={loading || !canSubmitSentence}
                    className="bg-indigo-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-indigo-700 transition disabled:opacity-50 flex items-center gap-2"
                  >
                    {loading ? (
                      'Processing...'
                    ) : (
                      <>
                        <Send className="w-4 h-4" />
                        Advise Me
                      </>
                    )}
                  </button>
                </div>
                <p className="text-xs text-gray-500">
                  The M3 Profiling Agent will extract your amount, horizon, risk taxonomy, goal purpose, and excluded categories from your sentence.
                </p>
              </div>
            )}
          </form>

          {pendingConfirm && (
            <NotesConfirm
              preview={pendingConfirm.preview}
              onConfirm={confirmNotes}
              onCancel={() => setPendingConfirm(null)}
              busy={loading}
            />
          )}

          {error && (
            <div className="mt-4 p-4 bg-red-50 text-red-700 rounded-lg flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
              <p>{error}</p>
            </div>
          )}
        </section>

        {/* Portfolio Upload Section */}
        <PortfolioUpload />

        {/* Results Dashboard */}
        {result && (
          <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Rule Set Badge & Combined Provenance Banner */}
            <div className="bg-white p-4 rounded-xl shadow-xs border border-gray-200">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2.5">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    Applied Selection Rules:
                  </span>
                  <span
                    className={`px-3 py-1 rounded-full text-xs font-bold border ${
                      result.rule_set === 'set2'
                        ? 'bg-purple-50 text-purple-700 border-purple-200'
                        : result.rule_set === 'combined'
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                        : 'bg-indigo-50 text-indigo-700 border-indigo-200'
                    }`}
                  >
                    {result.rule_set === 'set2'
                      ? 'Set 2 · Magic of MF'
                      : result.rule_set === 'combined'
                      ? 'Combined · Set 1 + 2'
                      : 'Set 1 · Default'}
                  </span>
                  {result.include_stocks && (
                    <span className="px-3 py-1 rounded-full text-xs font-bold border bg-emerald-50 text-emerald-700 border-emerald-200">
                      Stocks:{' '}
                      {result.stock_rule_set === 'set1'
                        ? 'Set 1 · Classic Heuristics'
                        : 'Set 2 · FA Course Notes (10-Pt)'}
                    </span>
                  )}
                  {result.include_stocks && result.trend_based_stocks && (
                    <span className="px-3 py-1 rounded-full text-xs font-bold border bg-sky-50 text-sky-700 border-sky-200">
                      Trend-based
                      {result.trend_regime?.regime && result.trend_regime.regime !== 'unknown'
                        ? ` · ${result.trend_regime.regime} market`
                        : ''}
                    </span>
                  )}
                  {result.input_mode && (
                    <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-gray-100 text-gray-700 border border-gray-200">
                      Input:{' '}
                      {result.input_mode === 'structured'
                        ? 'Direct Structured'
                        : result.input_mode === 'hybrid'
                        ? 'Hybrid (Structured + AI)'
                        : 'Natural Language'}
                    </span>
                  )}
                  {result.rule_set === 'combined' && (
                    <span className="text-xs text-gray-500 hidden sm:inline">
                      Strictest filters, blended weights & overlapping allocation bands
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  {result.rule_set === 'combined' && result.provenance && result.provenance.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowProvenanceDetails((prev) => !prev)}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold text-emerald-700 bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 transition cursor-pointer"
                    >
                      <Sparkles className="w-3.5 h-3.5" />
                      <span>{showProvenanceDetails ? 'Hide Merge Details' : `Merge Summary (${result.provenance.length})`}</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedRuleId(null)
                      setIsRulesViewerOpen(true)
                    }}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-800 transition cursor-pointer"
                  >
                    <span>Inspect Rules</span>
                    <ExternalLink className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Collapsible Provenance Cards */}
              {result.rule_set === 'combined' && showProvenanceDetails && result.provenance && (
                <div className="mt-4 pt-3 border-t border-gray-100">
                  <p className="text-xs font-medium text-emerald-900 mb-2">
                    Deterministic merge decisions applied for this advice portfolio:
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                    {result.provenance.map((p: any, idx: number) => (
                      <div
                        key={idx}
                        className={`p-2.5 rounded-lg border bg-gray-50/70 text-[11px] ${
                          p.conflict ? 'border-amber-300 bg-amber-50/60' : 'border-gray-200'
                        }`}
                      >
                        <div className="flex items-center justify-between font-bold text-gray-800 mb-0.5">
                          <span className="truncate pr-1">{p.setting}</span>
                          <span
                            className={`px-1.5 py-0.2 rounded text-[10px] ${
                              p.conflict ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'
                            }`}
                          >
                            {p.winner}
                          </span>
                        </div>
                        <p className="text-gray-500 line-clamp-2" title={p.reason}>
                          {p.reason}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {result.stock_warnings && result.stock_warnings.length > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-100">
                  <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800 space-y-1">
                    {result.stock_warnings.map((w: string, idx: number) => (
                      <p key={idx}>• {w}</p>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <SpecReport report={result.spec_report} />

            {/* Top Stats */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center gap-4">
                <div className="p-3 bg-blue-50 text-blue-600 rounded-full">
                  <Activity className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-sm text-gray-500">Risk Profile</p>
                  <p className="text-xl font-bold capitalize">{result.profile.category}</p>
                  <p className="text-xs text-gray-400">
                    Score: {result.profile.risk_score.toFixed(1)}/10 • Profile v
                    {result.profile.version}
                  </p>
                </div>
              </div>

              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center gap-4">
                <div className="p-3 bg-green-50 text-green-600 rounded-full">
                  <Shield className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-sm text-gray-500">Goal Horizon</p>
                  <p className="text-xl font-bold">{goalHorizon} Years</p>
                  <p className="text-xs text-gray-400">
                    Preference:{' '}
                    {(result.goal?.risk_preference || '').replace(/_/g, ' ') || 'stated'}
                  </p>
                  {result.goal?.purpose && (
                    <p className="text-xs text-indigo-600 font-medium mt-0.5">
                      Purpose: {result.goal.purpose}
                    </p>
                  )}
                </div>
              </div>

              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center gap-4">
                <div className="p-3 bg-purple-50 text-purple-600 rounded-full">
                  <TrendingUp className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-sm text-gray-500">Goal Amount</p>
                  <p className="text-xl font-bold">₹{Number(goalAmount).toLocaleString()}</p>
                  {totalAllocatedAmount > 0 && totalAllocatedAmount !== goalAmount && (
                    <p className="text-xs text-gray-400">
                      Allocated (₹500 steps): ₹{totalAllocatedAmount.toLocaleString()}
                    </p>
                  )}
                  {result.goal?.excluded_categories?.length > 0 && (
                    <p className="text-xs text-red-600 mt-0.5">
                      Excluded: {result.goal.excluded_categories.join(', ')}
                    </p>
                  )}
                </div>
              </div>
            </div>

            {/* Main Content Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
              {/* Target Allocation */}
              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 lg:col-span-1">
                <h3 className="text-lg font-semibold mb-2">Target Allocation</h3>
                <p className="text-xs text-gray-500 mb-4">
                  Deterministic midpoint within risk-adjusted bands
                </p>
                <div className="h-60 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={allocationData}
                        cx="50%"
                        cy="50%"
                        innerRadius={52}
                        outerRadius={76}
                        paddingAngle={5}
                        dataKey="value"
                      >
                        {allocationData.map((_, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <RechartsTooltip formatter={(value: any) => `${Number(value ?? 0).toFixed(1)}%`} />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                {result.assumptions && (
                  <div className="mt-4 pt-4 border-t border-gray-100 text-xs text-gray-500 space-y-1">
                    <p className="font-semibold text-gray-700">Assumed Profile Defaults:</p>
                    <p>Savings rate: {((result.assumptions.savings_rate ?? 0.2) * 100).toFixed(0)}%</p>
                    <p>Emergency buffer: {result.assumptions.emergency_months ?? 6} months</p>
                    <p className="capitalize">
                      Income stability: {result.assumptions.income_stability ?? 'stable'}
                    </p>
                  </div>
                )}
              </div>

              {/* Recommendations */}
              <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 lg:col-span-3">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                  <div>
                    <h3 className="text-lg font-semibold">Recommended Portfolio</h3>
                    <p className="text-xs text-gray-500">
                      Click any mutual fund to open its full research dossier, NAV history & audit
                      citations in a new tab.
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs font-semibold px-2.5 py-1 rounded ${
                        (result.data_mode || activeMode) === 'real'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : 'bg-indigo-50 text-indigo-700 border border-indigo-200'
                      }`}
                    >
                      {(result.data_mode || activeMode) === 'real'
                        ? 'Real Market Data (AMFI / mfapi.in)'
                        : 'Synthetic Demo Data'}
                    </span>
                    <span className="text-xs font-mono bg-gray-100 text-gray-600 px-2.5 py-1 rounded">
                      Run ID: {result.run_id}
                    </span>
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="text-gray-500 border-b border-gray-200 text-xs uppercase tracking-wider">
                        <th className="pb-3 font-medium">Fund & Grounded Rationale</th>
                        <th className="pb-3 font-medium">Bucket / Category</th>
                        <th className="pb-3 font-medium text-right">ER %</th>
                        <th className="pb-3 font-medium text-right">Rating</th>
                        <th className="pb-3 font-medium text-right">5Y CAGR</th>
                        <th className="pb-3 font-medium text-right">Score</th>
                        <th className="pb-3 font-medium text-right">Weight</th>
                        <th className="pb-3 font-medium text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {result.recommendations.map((rec: any, i: number) => {
                        const isStock = rec.instrument_type === 'stock'
                        const code = rec.instrument_id || rec.citations?.[0]?.split(':')[0] || ''
                        const runMode = result.data_mode || activeMode
                        const detailHref = code
                          ? `/fund/${encodeURIComponent(code)}?run_id=${encodeURIComponent(result.run_id)}&data_mode=${encodeURIComponent(runMode)}`
                          : '#'

                        const { proText, watchText, inlineCitations } = parseRationale(
                          rec.rationale || ''
                        )
                        const citeIds: string[] =
                          rec.citations && rec.citations.length > 0
                            ? rec.citations
                            : inlineCitations

                        return (
                          <tr key={i} className="group hover:bg-indigo-50/30 transition align-top">
                            <td className="py-4 pr-4">
                              <div className="flex flex-wrap items-center gap-2">
                                {isStock ? (
                                  <button
                                    type="button"
                                    onClick={() => code && openAnalyzeStock(code)}
                                    className="font-semibold text-emerald-700 hover:text-emerald-900 hover:underline inline-flex items-center gap-1.5 cursor-pointer"
                                    title="Open stock fundamental analysis dossier"
                                  >
                                    <span>{rec.name}</span>
                                    <ExternalLink className="w-3.5 h-3.5 opacity-75 group-hover:opacity-100" />
                                  </button>
                                ) : (
                                  <a
                                    href={detailHref}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="font-semibold text-indigo-700 hover:text-indigo-900 hover:underline inline-flex items-center gap-1.5"
                                    title="Open full fund research dossier in a new tab"
                                  >
                                    <span>{rec.name}</span>
                                    <ExternalLink className="w-3.5 h-3.5 opacity-75 group-hover:opacity-100" />
                                  </a>
                                )}
                                <span
                                  className={`px-2 py-0.5 text-[10px] font-bold uppercase rounded ${
                                    isStock
                                      ? 'bg-emerald-700 text-white'
                                      : 'bg-emerald-100 text-emerald-800'
                                  }`}
                                >
                                  {isStock ? 'STOCK' : rec.action || 'BUY'}
                                </span>
                                {code && (
                                  <span className="text-[11px] font-mono text-gray-400">
                                    {code}
                                  </span>
                                )}
                                {isStock && code && (
                                  <Stock360Button
                                    symbol={code}
                                    badge={rec.report360}
                                    runId={result.run_id}
                                    context="advise"
                                    horizonYears={result.goal?.horizon_years}
                                  />
                                )}
                                {isStock && rec.checks_passed && (
                                  <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded bg-indigo-50 text-indigo-700 border border-indigo-200">
                                    Checks: {rec.checks_passed}
                                  </span>
                                )}
                                {isStock && rec.valuation_status && (
                                  <span
                                    className={`px-1.5 py-0.5 text-[10px] font-semibold rounded capitalize ${
                                      rec.valuation_status === 'undervalued'
                                        ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                        : rec.valuation_status === 'fair'
                                        ? 'bg-blue-50 text-blue-700 border border-blue-200'
                                        : 'bg-amber-50 text-amber-700 border border-amber-200'
                                    }`}
                                  >
                                    {rec.valuation_status}
                                  </span>
                                )}
                              </div>

                              <div className="text-xs text-gray-600 mt-1.5 max-w-xl space-y-1">
                                {proText && <p>{proText}</p>}
                                {watchText && (
                                  <p className="text-amber-800 bg-amber-50/80 px-2 py-1 rounded border border-amber-100">
                                    <span className="font-semibold">Watch:</span> {watchText}
                                  </p>
                                )}
                              </div>

                              {citeIds.length > 0 && (
                                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                                  <span className="text-[10px] text-gray-400 uppercase tracking-wider">
                                    Citations:
                                  </span>
                                  {citeIds.map((cid: string, idx: number) =>
                                    isStock ? (
                                      <button
                                        key={idx}
                                        type="button"
                                        onClick={() => code && openAnalyzeStock(code)}
                                        className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 hover:bg-emerald-100 hover:text-emerald-800 transition cursor-pointer"
                                        title="View verified source fact in stock analyzer"
                                      >
                                        {cid}
                                      </button>
                                    ) : (
                                      <a
                                        key={idx}
                                        href={`${detailHref}#fact-${cid}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 hover:bg-indigo-100 hover:text-indigo-800 transition"
                                        title="View verified source fact in dossier"
                                      >
                                        {cid}
                                      </a>
                                    )
                                  )}
                                </div>
                              )}
                            </td>

                            <td className="py-4 pr-3 whitespace-nowrap">
                              <span
                                className={`inline-block px-2 py-0.5 rounded text-xs font-medium capitalize ${
                                  isStock
                                    ? 'bg-emerald-50 text-emerald-700'
                                    : 'bg-indigo-50 text-indigo-700'
                                }`}
                              >
                                {rec.bucket}
                              </span>
                              {rec.category && (
                                <p className="text-xs text-gray-500 mt-1">{rec.category}</p>
                              )}
                              {isStock && rec.sector && (
                                <p className="text-[11px] text-emerald-700 font-medium mt-0.5">
                                  {rec.sector}
                                </p>
                              )}
                            </td>

                            <td className="py-4 px-2 text-right whitespace-nowrap text-gray-700">
                              {isStock
                                ? '0.00%'
                                : rec.expense_ratio != null
                                ? `${rec.expense_ratio.toFixed(2)}%`
                                : '-'}
                            </td>

                            <td className="py-4 px-2 text-right whitespace-nowrap">
                              {isStock ? (
                                <span className="text-xs font-medium text-emerald-700">
                                  Direct Eq
                                </span>
                              ) : rec.rating != null ? (
                                <span className="font-medium text-amber-600">
                                  {rec.rating.toFixed(0)}★
                                </span>
                              ) : (
                                <span
                                  className="text-xs text-gray-400"
                                  title="Index Funds and ETFs are passive and unrated"
                                >
                                  Passive
                                </span>
                              )}
                            </td>

                            <td className="py-4 px-2 text-right whitespace-nowrap font-medium text-emerald-700">
                              {rec.cagr_5y != null ? `${rec.cagr_5y.toFixed(2)}%` : '-'}
                            </td>

                            <td className="py-4 px-2 text-right whitespace-nowrap">
                              {rec.score != null ? (
                                <div className="flex flex-col items-end gap-0.5">
                                  <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-800 font-semibold text-xs">
                                    {rec.score.toFixed(1)}
                                  </span>
                                  {rec.sub_scores?.news != null && (
                                    <span
                                      className="text-[10px] text-gray-400"
                                      title={`News sub-score: ${Number(rec.sub_scores.news).toFixed(0)}/100 • Sentiment sub-score: ${Number(rec.sub_scores.sentiment ?? 50).toFixed(0)}/100`}
                                    >
                                      N:{Number(rec.sub_scores.news).toFixed(0)} S:{Number(rec.sub_scores.sentiment ?? 50).toFixed(0)}
                                    </span>
                                  )}
                                </div>
                              ) : (
                                '-'
                              )}
                            </td>

                            <td className="py-4 px-2 text-right whitespace-nowrap font-semibold text-gray-900">
                              {rec.weight.toFixed(1)}%
                            </td>

                            <td className="py-4 pl-2 text-right whitespace-nowrap">
                              <p className="font-semibold text-gray-900">
                                ₹{rec.amount.toLocaleString()}
                              </p>
                              {rec.raw_amount != null &&
                                Math.abs(rec.raw_amount - rec.amount) >= 1 && (
                                  <p
                                    className="text-[10px] text-gray-400"
                                    title="Amounts are rounded down to ₹500 SIP-friendly steps"
                                  >
                                    exact ₹{rec.raw_amount.toLocaleString()}
                                  </p>
                                )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>

                {result.unallocated_weight > 0.01 && (
                  <div className="mt-4 p-3 bg-yellow-50 text-yellow-800 rounded-lg text-sm">
                    <span className="font-semibold">Note:</span>{' '}
                    {result.unallocated_weight.toFixed(1)}% of the portfolio could not be allocated
                    to suitable instruments.
                  </div>
                )}
              </div>
            </div>

            {/* Dedicated Separate Section: Real-Time News & Market Context */}
            {result.news_context &&
              ((result.news_context.fund_news && result.news_context.fund_news.length > 0) ||
                (result.news_context.macro_news && result.news_context.macro_news.length > 0)) && (
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 space-y-5">
                  <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 pb-4">
                    <div className="flex items-start gap-3">
                      <div className="p-2.5 rounded-lg bg-amber-50 text-amber-700 border border-amber-200 mt-0.5">
                        <Newspaper className="w-5 h-5" />
                      </div>
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-lg font-semibold text-gray-900">
                            Real-Time News & Market Context
                          </h3>
                          <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                            Factored into Analyst Sub-Scores (News & Sentiment)
                          </span>
                        </div>
                        <p className="text-xs text-gray-500 mt-0.5">
                          Headlines are deterministically tagged for materiality (High / Medium / Low) and sentiment (Positive / Neutral / Negative) and scored into each candidate fund&apos;s Analyst Verdict prior to portfolio sizing.
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      <span
                        className={`px-2.5 py-1 rounded-full font-medium border ${
                          result.news_context.live_rss_enabled
                            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                            : 'bg-gray-50 text-gray-600 border-gray-200'
                        }`}
                      >
                        {result.news_context.live_rss_enabled
                          ? 'Live Google News RSS + Regulatory Feed'
                          : 'Curated Regulatory & Category Intelligence'}
                      </span>
                      {result.news_context.as_of && (
                        <span className="text-gray-400 font-mono">
                          As of {result.news_context.as_of}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Macro & Regulatory Signals */}
                  {result.news_context.macro_news && result.news_context.macro_news.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2.5">
                        Macro & Regulatory Backdrop (SEBI • RBI • AMFI)
                      </h4>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {result.news_context.macro_news.slice(0, 6).map((item: any, idx: number) => (
                          <div
                            key={item.id || idx}
                            className="p-3.5 rounded-lg border border-gray-200 bg-gray-50/60 flex flex-col justify-between gap-2"
                          >
                            <div className="space-y-1.5">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span
                                  className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                                    item.sentiment === 'positive'
                                      ? 'bg-emerald-100 text-emerald-800'
                                      : item.sentiment === 'negative'
                                      ? 'bg-red-100 text-red-800'
                                      : 'bg-gray-200 text-gray-700'
                                  }`}
                                >
                                  {item.sentiment}
                                </span>
                                <span
                                  className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                                    item.materiality === 'high'
                                      ? 'bg-amber-100 text-amber-800'
                                      : 'bg-blue-50 text-blue-700'
                                  }`}
                                >
                                  {item.materiality} materiality
                                </span>
                                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium uppercase bg-white border border-gray-200 text-gray-600">
                                  {item.scope}
                                </span>
                              </div>
                              {item.url ? (
                                <a
                                  href={item.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-xs font-medium text-gray-800 hover:text-indigo-700 hover:underline line-clamp-2 inline-flex items-start gap-1"
                                >
                                  <span>{item.headline}</span>
                                  <ExternalLink className="w-3 h-3 flex-shrink-0 mt-0.5 opacity-60" />
                                </a>
                              ) : (
                                <p className="text-xs font-medium text-gray-800 line-clamp-2">
                                  {item.headline}
                                </p>
                              )}
                            </div>
                            <div className="flex items-center justify-between text-[11px] text-gray-400 pt-1 border-t border-gray-100">
                              <span className="truncate">{item.source}</span>
                              <span className="font-mono">{item.published_at}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Per-Fund News & Sentiment Intelligence */}
                  {result.news_context.fund_news && result.news_context.fund_news.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2.5">
                        Recommended Funds — News & Sentiment Breakdown
                      </h4>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {result.news_context.fund_news.map((fn: any) => (
                          <div
                            key={fn.code}
                            className="p-4 rounded-xl border border-gray-200 bg-white space-y-3"
                          >
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div>
                                <div className="flex items-center gap-2">
                                  <span className="font-semibold text-sm text-gray-900">
                                    {fn.name}
                                  </span>
                                  <span className="text-[11px] font-mono text-gray-400">
                                    {fn.code}
                                  </span>
                                </div>
                                <p className="text-xs text-gray-500">
                                  {fn.category} {fn.amc ? `• ${fn.amc}` : ''}
                                </p>
                              </div>
                              <div className="flex items-center gap-1.5">
                                <span
                                  className={`px-2 py-0.5 rounded text-xs font-bold ${
                                    fn.news_score >= 60
                                      ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                      : fn.news_score < 45
                                      ? 'bg-red-50 text-red-700 border border-red-200'
                                      : 'bg-gray-100 text-gray-700 border border-gray-200'
                                  }`}
                                >
                                  News: {Number(fn.news_score).toFixed(0)}/100
                                </span>
                                <span
                                  className={`px-2 py-0.5 rounded text-xs font-bold ${
                                    fn.sentiment_score >= 60
                                      ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                      : fn.sentiment_score < 45
                                      ? 'bg-red-50 text-red-700 border border-red-200'
                                      : 'bg-gray-100 text-gray-700 border border-gray-200'
                                  }`}
                                >
                                  Sentiment: {Number(fn.sentiment_score).toFixed(0)}/100
                                </span>
                              </div>
                            </div>

                            <div className="flex items-center gap-3 text-[11px] text-gray-500 bg-gray-50 px-3 py-1.5 rounded-lg">
                              <span className="text-emerald-700 font-medium">
                                +{fn.positive_count} Positive
                              </span>
                              <span>•</span>
                              <span className="text-gray-600 font-medium">
                                {fn.neutral_count} Neutral
                              </span>
                              <span>•</span>
                              <span className="text-red-700 font-medium">
                                -{fn.negative_count} Negative
                              </span>
                              {fn.high_materiality_negative > 0 && (
                                <span className="ml-auto px-1.5 py-0.5 rounded bg-red-100 text-red-800 font-semibold">
                                  {fn.high_materiality_negative} High-Materiality Alert
                                </span>
                              )}
                            </div>

                            <div className="space-y-2">
                              {(fn.items || []).slice(0, 3).map((item: any, idx: number) => (
                                <div
                                  key={item.id || idx}
                                  className="text-xs border-l-2 pl-2.5 py-0.5 border-gray-200 space-y-0.5"
                                  style={{
                                    borderLeftColor:
                                      item.sentiment === 'positive'
                                        ? '#10b981'
                                        : item.sentiment === 'negative'
                                        ? '#ef4444'
                                        : '#9ca3af',
                                  }}
                                >
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <span
                                      className={`text-[10px] font-bold uppercase ${
                                        item.sentiment === 'positive'
                                          ? 'text-emerald-700'
                                          : item.sentiment === 'negative'
                                          ? 'text-red-700'
                                          : 'text-gray-500'
                                      }`}
                                    >
                                      [{item.sentiment}]
                                    </span>
                                    <span className="text-[10px] text-gray-400 uppercase">
                                      {item.scope} • {item.materiality} materiality
                                    </span>
                                    <span className="text-[10px] text-gray-400 ml-auto font-mono">
                                      {item.source} ({item.published_at})
                                    </span>
                                  </div>
                                  {item.url ? (
                                    <a
                                      href={item.url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="text-gray-800 hover:text-indigo-700 hover:underline inline-flex items-center gap-1"
                                    >
                                      <span>{item.headline}</span>
                                      <ExternalLink className="w-3 h-3 opacity-60 flex-shrink-0" />
                                    </a>
                                  ) : (
                                    <p className="text-gray-800">{item.headline}</p>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </section>
              )}

            {/* Action Center */}
            {result.escalations && result.escalations.length > 0 && (
              <div className="bg-red-50 p-6 rounded-xl border border-red-200">
                <div className="flex items-center gap-3 mb-4">
                  <AlertTriangle className="w-6 h-6 text-red-600" />
                  <h3 className="text-lg font-semibold text-red-800">
                    Action Center - Attention Required
                  </h3>
                </div>
                <ul className="list-disc pl-5 space-y-2 text-red-700">
                  {result.escalations.map((esc: string, i: number) => (
                    <li key={i}>{esc}</li>
                  ))}
                </ul>
                <div className="mt-6 flex justify-end">
                  <button className="bg-red-600 text-white px-5 py-2 rounded-lg font-medium hover:bg-red-700 transition">
                    Escalate to Human Advisor
                  </button>
                </div>
              </div>
            )}

            {activeRunId && result.run_id === activeRunId && (
              <SavedReports360 runId={activeRunId} reports={historyItemQuery.data?.reports360} />
            )}

            {/* Disclosures */}
            {result.disclosures && result.disclosures.length > 0 && (
              <div className="bg-gray-100 p-6 rounded-xl border border-gray-200 text-xs text-gray-500">
                <h4 className="font-semibold text-gray-700 mb-2 uppercase tracking-wider">
                  Disclosures
                </h4>
                <ul className="list-disc pl-5 space-y-1">
                  {result.disclosures.map((d: string, i: number) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Grounded Follow-Up Chat */}
            {result.run_id && (
              <FollowUpChat
                runId={result.run_id}
                dataMode={result.data_mode || activeMode}
                recommendations={result.recommendations || []}
                initialMessages={historyMessages}
              />
            )}
          </div>
        )}
            </div>
          </div>
        )}

        {/* Rules Viewer Drawer */}
        <RulesViewer
          isOpen={isRulesViewerOpen}
          onClose={() => setIsRulesViewerOpen(false)}
          activeRuleSet={activeRuleSet}
          onSelectRuleSet={(newSet) => switchRuleSetMutation.mutate(newSet)}
          initialRuleId={selectedRuleId}
        />
        <Stock360Host />
      </div>
    </div>
  )
}

export default App

