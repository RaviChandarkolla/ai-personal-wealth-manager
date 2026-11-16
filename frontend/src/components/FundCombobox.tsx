import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { useQuery } from '@tanstack/react-query'
import { Search, ChevronDown, X, Check } from 'lucide-react'

const API_BASE = 'http://localhost:8000/api'

export interface SchemeSearchOption {
  code: string
  name: string
  amc?: string | null
  category: string
  bucket: string
  plan?: string | null
  option?: string | null
  expense_ratio?: number | null
  aum_cr?: number | null
  latest_nav?: number | null
  as_of: string
  source?: string
}

interface FundComboboxProps {
  label: string
  value: SchemeSearchOption | null
  onChange: (scheme: SchemeSearchOption | null) => void
  excludeCode?: string
  dataMode?: 'synthetic' | 'real'
  disabled?: boolean
}

export default function FundCombobox({
  label,
  value,
  onChange,
  excludeCode,
  dataMode,
  disabled = false,
}: FundComboboxProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [searchInput, setSearchInput] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [directOnly, setDirectOnly] = useState(true)
  const [growthOnly, setGrowthOnly] = useState(true)
  const [selectedCategory, setSelectedCategory] = useState<string>('')
  const [highlightIndex, setHighlightIndex] = useState(0)

  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // 250ms debounce for search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchInput.trim())
      setHighlightIndex(0)
    }, 250)
    return () => clearTimeout(timer)
  }, [searchInput])

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const categoriesQuery = useQuery({
    queryKey: ['compareCategories', dataMode],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/compare/categories`, {
        params: { data_mode: dataMode },
      })
      const raw = Array.isArray(res.data) ? res.data : res.data?.categories || []
      return raw.map((item: any) =>
        typeof item === 'string' ? item : item.category
      ) as string[]
    },
  })

  const schemesQuery = useQuery({
    queryKey: [
      'compareSchemes',
      debouncedQuery,
      selectedCategory,
      directOnly,
      growthOnly,
      dataMode,
    ],
    queryFn: async () => {
      const res = await axios.get(`${API_BASE}/compare/schemes`, {
        params: {
          q: debouncedQuery,
          category: selectedCategory || undefined,
          plan: directOnly ? 'direct' : 'all',
          option: growthOnly ? 'growth' : 'all',
          limit: 30,
          data_mode: dataMode,
        },
      })
      return (Array.isArray(res.data) ? res.data : res.data?.schemes || []) as SchemeSearchOption[]
    },
  })

  const options = (schemesQuery.data || []).filter((s) => s.code !== excludeCode)

  const handleSelect = (item: SchemeSearchOption) => {
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
        <div className="flex items-center gap-3 text-xs text-gray-600">
          <label className="inline-flex items-center gap-1 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={directOnly}
              onChange={(e) => setDirectOnly(e.target.checked)}
              disabled={disabled}
              className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <span>Direct only</span>
          </label>
          <label className="inline-flex items-center gap-1 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={growthOnly}
              onChange={(e) => setGrowthOnly(e.target.checked)}
              disabled={disabled}
              className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <span>Growth only</span>
          </label>
        </div>
      </div>

      {/* Selected value card or search trigger */}
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
              <div className="font-semibold text-sm text-gray-900 truncate" title={value.name}>
                {value.name}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-1">
                <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                  {value.code}
                </span>
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 font-medium">
                  {value.category}
                </span>
                {value.plan && (
                  <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 font-semibold">
                    {value.plan}
                  </span>
                )}
                {value.option && (
                  <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-semibold">
                    {value.option}
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
                  ? `${value.name} (${value.code})`
                  : 'Search by fund name, AMC, category, or AMFI code...'
              }
              className="w-full text-sm text-gray-900 bg-transparent focus:outline-none"
            />
            <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
          </div>
        )}
      </div>

      {/* Dropdown menu */}
      {isOpen && (
        <div className="absolute z-40 left-0 right-0 mt-1 bg-white rounded-xl shadow-lg border border-gray-200 overflow-hidden">
          {/* Optional Category Filter Bar */}
          {categoriesQuery.data && categoriesQuery.data.length > 0 && (
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-200 flex items-center justify-between gap-2">
              <span className="text-[11px] font-medium text-gray-500">Filter Category:</span>
              <select
                value={selectedCategory}
                onChange={(e) => {
                  setSelectedCategory(e.target.value)
                  setHighlightIndex(0)
                }}
                className="text-xs bg-white border border-gray-300 rounded-md px-2 py-1 text-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="">All Categories</option>
                {categoriesQuery.data.map((cat) => (
                  <option key={cat} value={cat}>
                    {cat}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="max-h-72 overflow-y-auto divide-y divide-gray-100">
            {schemesQuery.isLoading ? (
              <div className="p-4 text-xs text-gray-500 text-center">Searching schemes...</div>
            ) : options.length === 0 ? (
              <div className="p-4 text-xs text-gray-500 text-center">
                No matching mutual fund schemes found.
              </div>
            ) : (
              options.map((item, idx) => {
                const isHighlighted = idx === highlightIndex
                const isSelected = value?.code === item.code
                return (
                  <button
                    key={item.code}
                    type="button"
                    onMouseEnter={() => setHighlightIndex(idx)}
                    onClick={() => handleSelect(item)}
                    className={`w-full text-left px-3.5 py-2.5 transition flex items-start justify-between gap-2 ${
                      isHighlighted ? 'bg-indigo-50/80' : 'hover:bg-gray-50'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-semibold text-gray-900 truncate">
                        {item.name}
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 mt-1">
                        <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-gray-100 text-gray-600">
                          {item.code}
                        </span>
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-indigo-50 text-indigo-700 font-medium">
                          {item.category}
                        </span>
                        {item.plan && (
                          <span className="text-[10px] uppercase px-1.5 py-0.2 rounded bg-emerald-50 text-emerald-700 font-medium">
                            {item.plan}
                          </span>
                        )}
                        {item.option && (
                          <span className="text-[10px] uppercase px-1.5 py-0.2 rounded bg-blue-50 text-blue-700 font-medium">
                            {item.option}
                          </span>
                        )}
                        {item.expense_ratio != null && (
                          <span className="text-[10px] text-gray-500">
                            TER: {item.expense_ratio.toFixed(2)}%
                          </span>
                        )}
                        {item.aum_cr != null && (
                          <span className="text-[10px] text-gray-500">
                            AUM: ₹{item.aum_cr.toLocaleString('en-IN')} Cr
                          </span>
                        )}
                      </div>
                    </div>
                    {isSelected && <Check className="w-4 h-4 text-indigo-600 flex-shrink-0 mt-1" />}
                  </button>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}
