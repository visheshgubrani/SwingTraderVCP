import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type { CandleData } from "@/features/chart/TradingChart"
import { apiRequest } from "@/lib/api"

export type SyncState =
  | "idle"
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled"
  | "authentication_required"

export interface SyncStatus {
  run_id: string
  state: SyncState
  triggered_by: string | null
  is_running: boolean
  total_symbols: number
  current_index: number
  current_symbol: string
  successful_symbols: number
  skipped_symbols: number
  candles_upserted: number
  error_count: number
  errors: Array<{
    symbol: string
    error: string
    timestamp: string
  }>
  logs: string[]
  started_at: string | null
  completed_at: string | null
  db_metrics: {
    total_candles: number
    nifty500_instruments: number
    latest_candle_date: string | null
    symbols_at_latest_date: number
  }
  schedule: {
    enabled: boolean
    weekdays: string
    time: string
    timezone: string
  }
}

interface SyncTriggerResponse {
  status: "queued"
  run_id: string
  message: string
}

interface SyncCancelResponse {
  status: "cancelled"
  message: string
}

interface CandlesResponse {
  symbol: string
  timeframe: string
  candles: Array<{
    time: string
    open: number
    high: number
    low: number
    close: number
    volume?: number
  }>
}

export const historicalKeys = {
  all: ["historical"] as const,
  status: () => [...historicalKeys.all, "status"] as const,
  candles: () => [...historicalKeys.all, "candles"] as const,
  candleSeries: (symbol: string, timeframe: string, limit: number) =>
    [
      ...historicalKeys.candles(),
      { symbol, timeframe, limit },
    ] as const,
}

export function isSyncActive(status?: SyncStatus) {
  return status?.state === "queued" || status?.state === "running"
}

export function canScanAfterSync(status?: SyncStatus) {
  if (!status) return false
  if (status.state === "succeeded") return true
  if (status.state !== "partial" || status.total_symbols <= 0) return false

  const toleratedFailures = Math.max(
    1,
    Math.floor(status.total_symbols * 0.01),
  )
  return (
    status.successful_symbols >= status.total_symbols - toleratedFailures &&
    status.error_count <= toleratedFailures
  )
}

function normalizeCandles(response: CandlesResponse): CandleData[] {
  const candlesByDate = new Map<string, CandleData>()

  for (const candle of response.candles) {
    const date = candle.time.slice(0, 10)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue
    candlesByDate.set(date, {
      time: date,
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
      volume: Number(candle.volume ?? 0),
    })
  }

  return [...candlesByDate.values()].sort((left, right) =>
    left.time.localeCompare(right.time),
  )
}

export function useSyncStatus() {
  return useQuery({
    queryKey: historicalKeys.status(),
    queryFn: () => apiRequest<SyncStatus>("/historical/status"),
    staleTime: 1_000,
    refetchInterval: (query) =>
      isSyncActive(query.state.data) ? 1_500 : 5_000,
    retry: false,
  })
}

export function useTriggerSync() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (backfillYears: number) =>
      apiRequest<SyncTriggerResponse>("/historical/sync", {
        method: "POST",
        body: JSON.stringify({ backfill_years: backfillYears }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: historicalKeys.status(),
      })
    },
  })
}

export function useCancelSync() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiRequest<SyncCancelResponse>("/historical/cancel", {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: historicalKeys.status(),
      })
    },
  })
}

export function useCandles(
  symbol: string,
  timeframe = "1d",
  limit = 300,
) {
  const search = new URLSearchParams({
    symbol,
    timeframe,
    limit: String(limit),
  })

  return useQuery({
    queryKey: historicalKeys.candleSeries(symbol, timeframe, limit),
    queryFn: () =>
      apiRequest<CandlesResponse>(
        `/historical/candles?${search.toString()}`,
      ).then(normalizeCandles),
    enabled: symbol.length > 0,
    staleTime: 5 * 60_000,
    retry: 1,
  })
}
