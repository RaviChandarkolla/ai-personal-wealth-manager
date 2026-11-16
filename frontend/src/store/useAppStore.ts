import { create } from 'zustand'

export type RiskPreferenceType =
  | 'capital_preservation'
  | 'conservative'
  | 'moderate'
  | 'growth'
  | 'aggressive'

export type InputTabMode = 'hybrid' | 'sentence'
export type StockRuleSetId = 'set1' | 'set2'

export interface Open360 {
  symbol: string
  reportId?: string
  runId?: string
  context?: 'advise' | 'analyzer' | 'compare' | 'history'
  horizonYears?: number
}

export interface AppState {
  open360: Open360 | null
  openStock360: (target: Open360) => void
  closeStock360: () => void
  active360Jobs: Record<string, string>
  setActive360Job: (symbol: string, jobId: string | null) => void

  inputTab: InputTabMode
  setInputTab: (tab: InputTabMode) => void

  amount: number
  setAmount: (amount: number) => void

  horizonYears: number
  setHorizonYears: (years: number) => void

  riskPreference: RiskPreferenceType
  setRiskPreference: (risk: RiskPreferenceType) => void

  purpose: string
  setPurpose: (purpose: string) => void

  excludedCategories: string[]
  setExcludedCategories: (categories: string[]) => void
  toggleExcludedCategory: (category: string) => void

  emergencyMonths: number | null
  setEmergencyMonths: (months: number | null) => void

  savingsRate: number | null
  setSavingsRate: (rate: number | null) => void

  incomeStability: 'stable' | 'variable' | null
  setIncomeStability: (stability: 'stable' | 'variable' | null) => void

  currentGoalQuery: string
  setGoalQuery: (query: string) => void

  portfolioUploaded: boolean
  setPortfolioUploaded: (status: boolean) => void

  activeRunId: string | null
  setActiveRunId: (id: string | null) => void

  historySidebarOpen: boolean
  setHistorySidebarOpen: (open: boolean) => void
  toggleHistorySidebar: () => void

  includeStocks: boolean
  setIncludeStocks: (include: boolean) => void

  stockRuleSet: 'set1' | 'set2'
  setStockRuleSet: (ruleSet: 'set1' | 'set2') => void

  trendBasedStocks: boolean
  stock360Veto: boolean
  setStock360Veto: (val: boolean) => void
  stock360MinSellSignals: number
  setStock360MinSellSignals: (val: number) => void
  setTrendBasedStocks: (val: boolean) => void

  minMarketCapCr: number | null
  setMinMarketCapCr: (val: number | null) => void

  minTradedValueCr: number | null
  setMinTradedValueCr: (val: number | null) => void

  selectedAnalyzeSymbol: string
  setSelectedAnalyzeSymbol: (symbol: string) => void
}

export const useAppStore = create<AppState>()((set) => ({
  open360: null,
  openStock360: (open360) => set({ open360 }),
  closeStock360: () => set({ open360: null }),
  active360Jobs: {},
  setActive360Job: (symbol, jobId) =>
    set((state) => {
      const next = { ...state.active360Jobs }
      if (jobId) next[symbol] = jobId
      else delete next[symbol]
      return { active360Jobs: next }
    }),

  inputTab: 'hybrid',
  setInputTab: (inputTab) => set({ inputTab }),

  amount: 500000,
  setAmount: (amount) => set({ amount }),

  horizonYears: 5,
  setHorizonYears: (horizonYears) => set({ horizonYears }),

  riskPreference: 'moderate',
  setRiskPreference: (riskPreference) => set({ riskPreference }),

  purpose: '',
  setPurpose: (purpose) => set({ purpose }),

  excludedCategories: [],
  setExcludedCategories: (excludedCategories) => set({ excludedCategories }),
  toggleExcludedCategory: (category) =>
    set((state) => ({
      excludedCategories: state.excludedCategories.includes(category)
        ? state.excludedCategories.filter((c) => c !== category)
        : [...state.excludedCategories, category],
    })),

  emergencyMonths: null,
  setEmergencyMonths: (emergencyMonths) => set({ emergencyMonths }),

  savingsRate: null,
  setSavingsRate: (savingsRate) => set({ savingsRate }),

  incomeStability: null,
  setIncomeStability: (incomeStability) => set({ incomeStability }),

  currentGoalQuery: '',
  setGoalQuery: (query) => set({ currentGoalQuery: query }),

  portfolioUploaded: false,
  setPortfolioUploaded: (status) => set({ portfolioUploaded: status }),

  activeRunId: null,
  setActiveRunId: (activeRunId) => set({ activeRunId }),

  historySidebarOpen: true,
  setHistorySidebarOpen: (historySidebarOpen) => set({ historySidebarOpen }),
  toggleHistorySidebar: () =>
    set((state) => ({ historySidebarOpen: !state.historySidebarOpen })),

  includeStocks: false,
  setIncludeStocks: (includeStocks) => set({ includeStocks }),

  stockRuleSet: 'set2',
  setStockRuleSet: (stockRuleSet) => set({ stockRuleSet }),

  trendBasedStocks: true,
  stock360Veto: false,
  setStock360Veto: (stock360Veto) => set({ stock360Veto }),
  stock360MinSellSignals: 5,
  setStock360MinSellSignals: (stock360MinSellSignals) => set({ stock360MinSellSignals }),
  setTrendBasedStocks: (trendBasedStocks) => set({ trendBasedStocks }),

  minMarketCapCr: null,
  setMinMarketCapCr: (minMarketCapCr) => set({ minMarketCapCr }),

  minTradedValueCr: null,
  setMinTradedValueCr: (minTradedValueCr) => set({ minTradedValueCr }),

  selectedAnalyzeSymbol: 'INFY',
  setSelectedAnalyzeSymbol: (selectedAnalyzeSymbol) => set({ selectedAnalyzeSymbol }),
}))

