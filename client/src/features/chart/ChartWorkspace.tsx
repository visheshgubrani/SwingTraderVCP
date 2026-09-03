import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import { DatabaseZapIcon } from "lucide-react"

import { useToast } from "@/components/terminal/toast"
import { TradingChart } from "@/features/chart/TradingChart"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { historicalKeys, useCandles, useSyncStatus } from "@/features/historical/api"
import { defaultScanRunId, useScanResults, useScanRuns, type ScanResult } from "@/features/screener/api"
import { useVcpVisionAnalysis } from "@/features/screener/vcpVision"
import { useExecutionStatus } from "@/features/trade/api"
import { TradeExecutionForm } from "@/features/trade/TradeExecutionForm"
import { useMarketData } from "@/lib/MarketWSContext"
import { shortSymbol } from "@/lib/marketSymbols"
import { fmtNum } from "@/lib/format"
import { cn } from "@/lib/utils"

type TicketSide = "buy" | "sell" | null

function EmptyChart({ text }: { text: string }) {
  return (
    <div className="grid h-full place-content-center gap-2 bg-background p-6 text-center">
      <DatabaseZapIcon aria-hidden="true" className="mx-auto h-5 w-5 text-muted-text" />
      <p className="max-w-md text-xs text-muted-foreground">{text}</p>
    </div>
  )
}

/** Chart workspace (design chart view): clean full-height chart + floating quick trade + ticket. */
export function ChartWorkspace() {
  const { chartSymbol } = useTradingAppContext()
  const { ltpMap } = useMarketData()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryCache = useQueryClient()
  const syncStatus = useSyncStatus()
  const execution = useExecutionStatus()

  const [ticketSide, setTicketSide] = useState<TicketSide>(null)
  const [stopLossPrice, setStopLossPrice] = useState(0)
  const [targetPrice, setTargetPrice] = useState(0)
  const [defaulted, setDefaulted] = useState(false)
  const lastInvalidatedSync = useRef<string | null>(null)

  // Scanner meta for the current symbol (traceable ticket).
  const scanRuns = useScanRuns()
  const runId = useMemo(() => (scanRuns.data ? defaultScanRunId(scanRuns.data) : null), [scanRuns.data])
  const activeRun = useMemo(
    () => scanRuns.data?.find((run) => run.id === runId && run.status === "succeeded") ?? null,
    [runId, scanRuns.data],
  )
  const scanResults = useScanResults(runId, activeRun?.status)
  const results = scanResults.data ?? []

  const symbol = chartSymbol
  const meta: ScanResult | undefined = useMemo(
    () => (symbol ? results.find((result) => result.fyers_symbol === symbol) : undefined),
    [results, symbol],
  )
  const isIndex = (symbol ?? "").includes("INDEX")

  // Default symbol: ?symbol= wins; otherwise the top scanner result.
  useEffect(() => {
    if (defaulted || symbol || results.length === 0) return
    const first = results[0]?.fyers_symbol
    if (first) {
      setDefaulted(true)
      setSearchParams({ symbol: first }, { replace: true })
    }
  }, [defaulted, results, setSearchParams, symbol])

  // Plan flow: /?symbol=…&plan=1 opens the ticket panel prefilled BUY.
  useEffect(() => {
    if (searchParams.get("plan") !== "1" || !symbol) return
    if (isIndex) {
      toast("warn", { title: `${shortSymbol(symbol)} is an index — not tradable in this account.` })
    } else {
      setTicketSide("buy")
    }
    const next = new URLSearchParams(searchParams)
    next.delete("plan")
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol])

  // Default view is 2 years and more (750 daily sessions)
  const candlesQuery = useCandles(symbol ?? "", "1d", 750)
  const candles = candlesQuery.data ?? []
  const viewBars = candles

  const tick = symbol ? ltpMap.get(symbol) : undefined
  const lastCandle = candles.length > 0 ? candles[candles.length - 1]! : null
  const liveLtp = tick?.ltp ?? lastCandle?.close ?? 0

  // Vision overlay when the symbol has a succeeded advisory analysis.
  const visionAnalysis = useVcpVisionAnalysis(meta?.vcp_vision?.id ?? null)
  const visionOverlay = useMemo(() => {
    const analysis = visionAnalysis.data
    if (!analysis || analysis.status !== "succeeded" || !analysis.result) return null
    const derived = analysis.result.derived
    if (!derived?.contractions?.length) return null
    return {
      contractions: derived.contractions.map((contraction) => ({
        label: contraction.label,
        start: contraction.start,
        end: contraction.end,
        high: contraction.high,
        low: contraction.low,
      })),
      pivotPrice: derived.pivot_price ?? null,
    }
  }, [visionAnalysis.data])

  // Invalidate the candle cache when the EOD sync completes.
  useEffect(() => {
    const completedAt = syncStatus.data?.completed_at
    if (syncStatus.data?.state !== "succeeded" || !completedAt || lastInvalidatedSync.current === completedAt) return
    lastInvalidatedSync.current = completedAt
    void queryCache.invalidateQueries({ queryKey: historicalKeys.candles() })
  }, [queryCache, syncStatus.data])

  const openTicket = useCallback(
    (side: "buy" | "sell") => {
      if (!symbol) return
      if (isIndex) {
        toast("warn", { title: `${shortSymbol(symbol)} is an index — not tradable in this account.` })
        return
      }
      setTicketSide(side)
    },
    [isIndex, symbol, toast],
  )

  const rangeText =
    viewBars.length > 1
      ? `${fmtRange(viewBars[0]!.time, viewBars[viewBars.length - 1]!.time)} · ${viewBars.length} sessions`
      : "—"

  const mode = execution.data?.execution_mode.toUpperCase() ?? "PAPER"

  return (
    <section className="view h-full w-full">
      {/* Chart + optional ticket panel */}
      <div className="flex min-h-0 flex-1 w-full h-full">
        <div className="chartbox min-w-0 flex-1 h-full w-full">
          {!symbol ? (
            <EmptyChart text="Pick a symbol from the watchlist, the search box, or the scanner scoreboard." />
          ) : (
            <>
              <TradingChart
                data={viewBars}
                liveLtp={liveLtp}
                stopLossPrice={stopLossPrice}
                symbol={symbol}
                targetPrice={targetPrice}
                visionOverlay={visionOverlay}
                volumeVisible={true}
              />
              {candlesQuery.isLoading && (
                <div className="absolute inset-0 flex items-center justify-center bg-background/80 text-sm text-muted-foreground">
                  Loading daily candles from Postgres…
                </div>
              )}
              {candlesQuery.isError && (
                <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6">
                  <EmptyChart
                    text={
                      candlesQuery.error instanceof Error
                        ? candlesQuery.error.message
                        : "The candle API is unavailable."
                    }
                  />
                </div>
              )}
              {candlesQuery.isSuccess && candles.length === 0 && (
                <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6">
                  <EmptyChart text="No daily candles stored — run the EOD sync, then retry this symbol." />
                </div>
              )}

              {/* Floating Quick Trade BUY & SELL buttons (TradingView style on bottom-right) */}
              {symbol && !isIndex && (
                <div className="absolute bottom-10 right-20 z-20 flex items-center gap-1.5 rounded-lg border border-[#263246] bg-[#101826]/90 p-1.5 shadow-xl backdrop-blur-md">
                  <button
                    className={cn(
                      "flex items-center gap-1.5 rounded px-3 py-1.5 font-mono text-xs font-bold transition-all",
                      ticketSide === "buy"
                        ? "bg-[#22c55e] text-black shadow-md ring-2 ring-[#22c55e]/50"
                        : "bg-[#22c55e]/20 text-[#22c55e] hover:bg-[#22c55e] hover:text-black",
                    )}
                    onClick={() => openTicket("buy")}
                    title={`Open BUY ticket for ${shortSymbol(symbol)}`}
                    type="button"
                  >
                    <span>BUY</span>
                    {liveLtp > 0 && <span className="opacity-85">{fmtNum(liveLtp)}</span>}
                  </button>
                  <button
                    className={cn(
                      "flex items-center gap-1.5 rounded px-3 py-1.5 font-mono text-xs font-bold transition-all",
                      ticketSide === "sell"
                        ? "bg-[#ef4444] text-white shadow-md ring-2 ring-[#ef4444]/50"
                        : "bg-[#ef4444]/20 text-[#ef4444] hover:bg-[#ef4444] hover:text-white",
                      execution.data?.execution_mode === "live" && "opacity-40 cursor-not-allowed",
                    )}
                    disabled={execution.data?.execution_mode === "live"}
                    onClick={() => openTicket("sell")}
                    title={
                      execution.data?.execution_mode === "live"
                        ? "Sell disabled in live mode"
                        : `Open SELL ticket for ${shortSymbol(symbol)}`
                    }
                    type="button"
                  >
                    <span>SELL</span>
                    {liveLtp > 0 && <span className="opacity-85">{fmtNum(liveLtp)}</span>}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {symbol && ticketSide && !isIndex && (
          <aside className="flex w-[340px] shrink-0 flex-col border-l bg-surface max-[1100px]:hidden">
            <div className="flex h-10 shrink-0 items-center justify-between border-b border-border px-4">
              <span className="mono text-[10px] font-bold tracking-[0.14em] text-muted-text">
                NEW ORDER · {mode} · CNC
              </span>
              <button
                aria-label="Close trade ticket"
                className="ibtn"
                onClick={() => setTicketSide(null)}
                title="Close (panel)"
                type="button"
              >
                ×
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <TradeExecutionForm
                currentLtp={liveLtp}
                initialSide={ticketSide ?? "buy"}
                key={`${symbol}:${ticketSide}`}
                onTradeConfirmed={() => {
                  setTicketSide(null)
                  setStopLossPrice(0)
                  setTargetPrice(0)
                  toast("ok", { title: "Trade instruction confirmed", mono: `${shortSymbol(symbol)} · ${mode}` })
                }}
                onValuesChange={(_entry, stop, target) => {
                  setStopLossPrice(stop)
                  setTargetPrice(target)
                }}
                screeningResultId={meta?.id ?? null}
                symbol={symbol}
              />
            </div>
          </aside>
        )}
      </div>

      {/* Status footer */}
      <footer className="cstat">
        <span>{symbol ? `EOD candles · NSE ${isIndex ? "IDX" : "EQ"}` : "Pick a symbol to begin"}</span>
        <span className="r">{rangeText}</span>
      </footer>
    </section>
  )
}

function fmtRange(first: string, last: string): string {
  const fmt = (value: string) => {
    const date = new Date(`${value}T00:00:00Z`)
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${String(date.getUTCFullYear()).slice(2)}`
  }
  return `${fmt(first)} – ${fmt(last)}`
}
