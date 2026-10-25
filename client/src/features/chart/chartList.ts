import { useCallback, useEffect, useMemo } from "react"

import { applyScanListFilters, SCAN_LIST_DEFAULTS, useScanRunResults } from "@/features/screener/scanList"
import type { ScanResult } from "@/features/screener/api"
import { useWatchlistItems, useWatchlists, type WatchlistSummary } from "@/features/watchlist/api"
import { useUrlState } from "@/lib/useUrlState"

/** Sidebar list the chart walks with prev/next: the scan, or watchlist tab 1–4. */
export type ChartListKey = "scan" | "wl1" | "wl2" | "wl3" | "wl4"

export const WATCHLIST_TABS = ["wl1", "wl2", "wl3", "wl4"] as const

export interface ChartListItem {
  fyers_symbol: string
  symbol: string
  name: string | null
  scan?: ScanResult
}

const LIST_STORAGE_KEY = "vcp.chart.list"

/** Chart-route query keys that describe the list context (everything but symbol). */
export const CHART_LIST_PARAMS = ["list", "run", "q", "grade", "sort", "dir"] as const

function isChartListKey(value: string | null | undefined): value is ChartListKey {
  return value === "scan" || (WATCHLIST_TABS as readonly string[]).includes(value ?? "")
}

function rememberedList(): ChartListKey {
  try {
    const stored = window.localStorage.getItem(LIST_STORAGE_KEY)
    return isChartListKey(stored) ? stored : "scan"
  } catch {
    return "scan"
  }
}

export function watchlistTitle(index: number, list?: WatchlistSummary | null): string {
  if (index === 0) return "Indices"
  return list?.name ?? `Watchlist ${index + 1}`
}

export function sortedWatchlists(lists: WatchlistSummary[] | undefined): WatchlistSummary[] {
  return [...(lists ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at))
}

/**
 * The list shown in the chart sidebar and walked by prev/next. URL params:
 * `list` (scan | wl1–wl4) and, for the scan, `run`, `q`, `grade`, `sort`, `dir`.
 */
export function useChartList() {
  const [params, setParams] = useUrlState({ list: "", ...SCAN_LIST_DEFAULTS })
  const list: ChartListKey = isChartListKey(params.list) ? params.list : rememberedList()

  useEffect(() => {
    try {
      window.localStorage.setItem(LIST_STORAGE_KEY, list)
    } catch {
      // Storage unavailable (private window) — the URL still carries the list.
    }
  }, [list])

  const setList = useCallback((next: ChartListKey) => setParams({ list: next }), [setParams])

  // Scan data is always loaded: the chart header shows scan metrics for the symbol.
  const scan = useScanRunResults(params.run || null)

  const watchlists = useWatchlists()
  const lists = useMemo(() => sortedWatchlists(watchlists.data), [watchlists.data])
  const watchlistIndex = list === "scan" ? -1 : Number(list.slice(2)) - 1
  const watchlist = watchlistIndex >= 0 ? (lists[watchlistIndex] ?? null) : null
  const watchlistItems = useWatchlistItems(watchlist?.id ?? "__none__", watchlist !== null)

  const filters = { q: params.q, grade: params.grade, sort: params.sort, dir: params.dir }
  const scanItems = useMemo(
    () => applyScanListFilters(scan.results, filters),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scan.results, filters.q, filters.grade, filters.sort, filters.dir],
  )

  const items: ChartListItem[] = useMemo(() => {
    if (list === "scan") {
      return scanItems.map((row) => ({ fyers_symbol: row.fyers_symbol, symbol: row.symbol, name: row.name, scan: row }))
    }
    return (watchlistItems.data ?? []).map((item) => ({
      fyers_symbol: item.fyers_symbol,
      symbol: item.symbol,
      name: item.name,
    }))
  }, [list, scanItems, watchlistItems.data])

  const isLoading = list === "scan"
    ? scan.scanRuns.isLoading || scan.resultsQuery.isLoading
    : watchlists.isLoading || watchlistItems.isLoading

  const label = list === "scan" ? "Scan" : watchlistTitle(watchlistIndex, watchlist)

  return {
    list,
    setList,
    params,
    setParams,
    label,
    scan,
    watchlists: lists,
    watchlistIndex,
    watchlist,
    watchlistItems,
    items,
    isLoading,
  }
}

export type ChartListState = ReturnType<typeof useChartList>
