import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { useQuery } from '@tanstack/react-query'
import { Search, ChevronDown, X, Check, ShieldCheck } from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

export interface StockSearchOption {
  symbol: string
  name: string
  sector: string
  industry?: string
  is_bank: boolean
  price?: number | null
  market_cap_cr?: number | null
  pe?: number | null
  roe?: number | null
  passed?: boolean
  passed_count?: number
  evaluated_count?: number
  composite_score?: number
  source?: string
}

interface StockComboboxProps {
  label: string
  value: StockSearchOption | null
  onChange: (stock: StockSearchOption | null) => void
  excludeSymbols?: string[]
  ruleSet?: 'set1' | 'set2'
  disabled?: boolean
}

export default function StockCombobox({
  label,
  value,
  onChange,
  excludeSymbols = [],
  ruleSet = 'set2',
  disabled = false,
}: StockComboboxProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [onlyPassing, setOnlyPassing] = useState(false)
  const [selectedSector, setSelectedSector] = useState<string>('')
  const [selectedIndexTag, setSelectedIndexTag] = useState<string>('')
  const [highlightIndex, setHighlightIndex] = useState(0)

  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchInput.trim())
      setHighlightIndex(0)
    }, 200)
    return () => clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const sectorsQuery = useQuery({
    queryKey: ['stockSectors'],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/stocks/sectors`)
      return (res.data?.sectors || []) as string[]
    },
  })

  const stocksQuery = useQuery({
    queryKey: [
      'stocksList',
      debouncedQuery,
      selectedSector,
      selectedIndexTag,
      onlyPassing,
      ruleSet,
    ],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/stocks/search`, {
        params: {
          q: debouncedQuery || undefined,
          sector: selectedSector || undefined,
          index_tag: selectedIndexTag || undefined,
          only_passing: onlyPassing || undefined,
          rule_set: ruleSet,
          limit: 50,
        },
      })
      return (res.data?.items || []) as StockSearchOption[]
    },
  })

  const options = (stocksQuery.data || []).filter(
    (s) => !excludeSymbols.includes(s.symbol)
  )

  const handleSelect = (item: StockSearchOption) => {
    onChange(item)
    setIsOpen(false)
    setSearchInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isOpen) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        e.preventDefault()
        setIsOpen(true)
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIndex((prev) => (options.length > 0 ? (prev + 1) % options.length : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIndex((prev) =>
        options.length > 0 ? (prev - 1 + options.length) % options.length : 0
      )
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (options[highlightIndex]) {
        handleSelect(options[highlightIndex])
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setIsOpen(false)
    }
  }

  return (
    <div ref={containerRef} className="relative space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="text-xs font-semibold uppercase tracking-wider text-gray-600">
          {label}
        </label>
        <label className="inline-flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={onlyPassing}
            onChange={(e) => setOnlyPassing(e.target.checked)}
            disabled={disabled}
            className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          />
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
          <span>Only fundamentally good ({ruleSet.toUpperCase()})</span>
        </label>
      </div>

      <div
        onClick={() => {
          if (!disabled) {
            setIsOpen(true)
            setTimeout(() => inputRef.current?.focus(), 20)
          }
        }}
        className={`w-full rounded-xl border bg-white p-3 transition cursor-pointer ${
          isOpen
            ? 'border-indigo-500 ring-2 ring-indigo-500/20'
            : 'border-gray-300 hover:border-gray-400'
        } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
      >
        {value && !isOpen ? (
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-mono font-bold text-sm text-indigo-700">
                  {value.symbol}
                </span>
                <span className="font-semibold text-sm text-gray-900 truncate" title={value.name}>
                  {value.name}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-1">
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 font-medium">
                  {value.sector}
                </span>
                {value.is_bank && (
                  <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 font-semibold">
                    BANK
                  </span>
                )}
                {value.passed != null && value.evaluated_count ? (
                  <span
                    className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                      value.passed
                        ? 'bg-emerald-50 text-emerald-700'
                        : 'bg-rose-50 text-rose-700'
                    }`}
                  >
                    {value.passed ? 'PASS' : 'FAIL'} ({value.passed_count}/{value.evaluated_count})
                  </span>
                ) : null}
                {value.price != null && (
                  <span className="text-[11px] text-gray-600 font-medium">
                    ₹{value.price.toLocaleString('en-IN')}
                  </span>
                )}
              </div>
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onChange(null)
              }}
              className="p-1 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100"
              title="Clear selection"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-gray-400 flex-shrink-0" />
            <input
              ref={inputRef}
              type="text"
              value={searchInput}
              onFocus={() => setIsOpen(true)}
              onChange={(e) => {
                setSearchInput(e.target.value)
                setIsOpen(true)
              }}
              onKeyDown={handleKeyDown}
              disabled={disabled}
              placeholder={
                value
                  ? `${value.symbol} — ${value.name}`
                  : 'Search by NSE ticker (INFY, HDFCBANK, TCS), company name, or sector...'
              }
              className="w-full text-sm text-gray-900 bg-transparent focus:outline-none"
            />
            <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
          </div>
        )}
      </div>

      {isOpen && (
        <div className="absolute z-40 w-full rounded-xl border border-gray-200 bg-white shadow-xl overflow-hidden">
          {/* Sector and Index Tag Filter Bar */}
          <div className="p-2.5 bg-gray-50 border-b border-gray-200 flex flex-wrap items-center gap-2">
            <select
              value={selectedSector}
              onChange={(e) => setSelectedSector(e.target.value)}
              className="text-xs rounded-lg border border-gray-300 bg-white px-2 py-1 text-gray-700"
            >
              <option value="">All Sectors</option>
              {(sectorsQuery.data || []).map((sec) => (
                <option key={sec} value={sec}>
                  {sec}
                </option>
              ))}
            </select>

            <div className="flex items-center gap-1">
              {[
                { id: '', label: 'All' },
                { id: 'NIFTY50', label: 'Nifty 50' },
                { id: 'NIFTY_BANK', label: 'Nifty Bank' },
                { id: 'NIFTY_IT', label: 'Nifty IT' },
              ].map((tag) => (
                <button
                  key={tag.id}
                  type="button"
                  onClick={() => setSelectedIndexTag(tag.id)}
                  className={`px-2 py-0.5 text-[11px] font-medium rounded-full transition ${
                    selectedIndexTag === tag.id
                      ? 'bg-indigo-600 text-white'
                      : 'bg-white border border-gray-300 text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  {tag.label}
                </button>
              ))}
            </div>
          </div>

          <div className="max-h-72 overflow-y-auto divide-y divide-gray-100">
            {stocksQuery.isLoading ? (
              <div className="p-4 text-center text-xs text-gray-500">Loading stocks...</div>
            ) : options.length === 0 ? (
              <div className="p-4 text-center text-xs text-gray-500">
                No matching NSE stocks found. Try syncing stocks or clearing filters.
              </div>
            ) : (
              options.map((item, idx) => {
                const isSelected = value?.symbol === item.symbol
                const isHighlighted = idx === highlightIndex
                return (
                  <div
                    key={item.symbol}
                    onClick={() => handleSelect(item)}
                    onMouseEnter={() => setHighlightIndex(idx)}
                    className={`px-3 py-2.5 cursor-pointer transition flex items-center justify-between gap-2 ${
                      isHighlighted ? 'bg-indigo-50/70' : 'hover:bg-gray-50'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-xs text-indigo-700">
                          {item.symbol}
                        </span>
                        <span className="text-xs font-medium text-gray-900 truncate">
                          {item.name}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 mt-0.5 text-[11px] text-gray-500">
                        <span>{item.sector}</span>
                        {item.market_cap_cr != null && (
                          <span>· MCap ₹{Math.round(item.market_cap_cr).toLocaleString('en-IN')} Cr</span>
                        )}
                        {item.pe != null && <span>· P/E {item.pe.toFixed(1)}x</span>}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 flex-shrink-0">
                      {item.evaluated_count ? (
                        <span
                          className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                            item.passed
                              ? 'bg-emerald-100 text-emerald-800'
                              : 'bg-gray-100 text-gray-600'
                          }`}
                        >
                          {item.passed_count}/{item.evaluated_count}
                        </span>
                      ) : (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
                          Unsynced
                        </span>
                      )}
                      {isSelected && <Check className="w-4 h-4 text-indigo-600" />}
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}
