import { useCallback, useEffect, useMemo } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"

import type { ChartListItem } from "@/features/chart/chartList"
import { candlesQueryOptions } from "@/features/historical/api"

/** Daily candles the chart requests per symbol (2+ years). */
export const CHART_CANDLE_LIMIT = 750

export interface ListPosition {
  index: number
  total: number
  prev: ChartListItem | null
  next: ChartListItem | null
}

/** Where `symbol` sits in `items`. Off-list symbols step into the list from its top. */
export function listPosition(items: ChartListItem[], symbol: string | null): ListPosition {
  const index = symbol ? items.findIndex((item) => item.fyers_symbol === symbol) : -1
  return {
    index,
    total: items.length,
    prev: index > 0 ? items[index - 1]! : null,
    next: index < 0 ? (items[0] ?? null) : (items[index + 1] ?? null),
  }
}

/** True when a key press belongs to a text field, menu or dialog rather than the chart. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true
  return target.closest('[role="dialog"], [role="alertdialog"], [role="listbox"], [role="menu"]') !== null
}

function dialogOpen(): boolean {
  return document.querySelector('[role="dialog"], [role="alertdialog"]') !== null
}

/**
 * Prev/next over the chart sidebar list. ↓ / J = next, ↑ / K = prev.
 * Navigation replaces the history entry so flipping through 50 charts does not
 * bury the page you came from. Neighbouring candles are prefetched so the next
 * chart paints immediately.
 */
export function useChartListNav(items: ChartListItem[], symbol: string | null) {
  const [, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const position = useMemo(() => listPosition(items, symbol), [items, symbol])

  const goTo = useCallback(
    (fyersSymbol: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("symbol", fyersSymbol)
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const goNext = useCallback(() => {
    if (position.next) goTo(position.next.fyers_symbol)
  }, [goTo, position.next])

  const goPrev = useCallback(() => {
    if (position.prev) goTo(position.prev.fyers_symbol)
  }, [goTo, position.prev])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return
      if (isTypingTarget(event.target) || dialogOpen()) return
      const key = event.key
      if (key === "ArrowDown" || key === "j" || key === "J") {
        event.preventDefault()
        goNext()
      } else if (key === "ArrowUp" || key === "k" || key === "K") {
        event.preventDefault()
        goPrev()
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [goNext, goPrev])

  // Warm the two charts on either side of the current one.
  useEffect(() => {
    if (position.index < 0) return
    for (const offset of [1, -1, 2, -2]) {
      const neighbour = items[position.index + offset]
      if (neighbour) {
        void queryClient.prefetchQuery(candlesQueryOptions(neighbour.fyers_symbol, "1d", CHART_CANDLE_LIMIT))
      }
    }
  }, [items, position.index, queryClient])

  return { ...position, goTo, goNext, goPrev }
}
