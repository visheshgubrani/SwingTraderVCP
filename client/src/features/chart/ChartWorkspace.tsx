import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import { CandlestickChartIcon, DatabaseZapIcon } from "lucide-react"

import { EmptyState } from "@/components/terminal/panel"
import { useToast } from "@/components/terminal/toast"
import { ChartHeader } from "@/features/chart/ChartHeader"
import { ChartSidebar } from "@/features/chart/ChartSidebar"
import { useChartList, type ChartListState } from "@/features/chart/chartList"
import { TradingChart } from "@/features/chart/TradingChart"
import { CHART_CANDLE_LIMIT, useChartListNav } from "@/features/chart/useChartListNav"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { historicalKeys, useCandles, useSyncStatus } from "@/features/historical/api"
import type { ScanResult } from "@/features/screener/api"
import { VcpVisionSheet } from "@/features/screener/VcpVisionSheet"
import { useVcpVisionAnalysis } from "@/features/screener/vcpVision"
import { useExecutionStatus } from "@/features/trade/api"
import { TradeExecutionForm } from "@/features/trade/TradeExecutionForm"
import { useMarketData } from "@/lib/MarketWSContext"
import { shortSymbol } from "@/lib/marketSymbols"
import { fmtDate, fmtNum } from "@/lib/format"
import { useStoredState } from "@/lib/useStoredState"
import { cn } from "@/lib/utils"

type TicketSide = "buy" | "sell" | null

/** Chart route: list sidebar (scan / watchlists) beside the chart workspace. */
export function ChartPage() {
  const chartList = useChartList()
  const { chartSymbol } = useTradingAppContext()
  const [collapsed, setCollapsed] = useStoredState("vcp.chart.sidebarCollapsed", false)

  return (
    <div className="flex h-full min-h-0 w-full max-[720px]:flex-col">
      {!collapsed && (
        <ChartSidebar activeSymbol={chartSymbol} chartList={chartList} onCollapse={() => setCollapsed(true)} />
      )}
      <ChartWorkspace chartList={chartList} onExpandSidebar={() => setCollapsed(false)} sidebarCollapsed={collapsed} />
    </div>
  )
}

/** Chart view: quote bar + full-height chart + optional order ticket. */
export function ChartWorkspace({
  chartList,
  sidebarCollapsed,
  onExpandSidebar,
}: {
  chartList: ChartListState
  sidebarCollapsed: boolean
  onExpandSidebar: () => void
}) {
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
  const [visionResult, setVisionResult] = useState<ScanResult | null>(null)
  const lastInvalidatedSync = useRef<string | null>(null)

  const symbol = chartSymbol
  const isIndex = (symbol ?? "").includes("INDEX")
  const isLive = execution.data?.execution_mode === "live"
  const mode = execution.data?.execution_mode.toUpperCase() ?? "PAPER"

  // Scan metrics come from the run the list is showing (URL `run`), not always the latest.
  const meta: ScanResult | undefined = useMemo(
    () => (symbol ? chartList.scan.results.find((result) => result.fyers_symbol === symbol) : undefined),
    [chartList.scan.results, symbol],
  )
  const listItem = chartList.items.find((item) => item.fyers_symbol === symbol)
  const nav = useChartListNav(chartList.items, symbol)

  // No symbol in the URL: open the first entry of the current list.
  const firstItem = chartList.items[0]?.fyers_symbol
  useEffect(() => {
    if (symbol || !firstItem) return
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set("symbol", firstItem)
        return next
      },
      { replace: true },
    )
  }, [firstItem, setSearchParams, symbol])

  const clearTicket = useCallback(() => {
    setTicketSide(null)
    setStopLossPrice(0)
    setTargetPrice(0)
  }, [])

  // A ticket and its SL/TP lines belong to one symbol; drop them on switch.
  useEffect(() => {
    clearTicket()
  }, [clearTicket, symbol])

  // Plan flow: /?symbol=…&plan=buy|sell (legacy plan=1 = buy) opens the ticket.
  const plan = searchParams.get("plan")
  useEffect(() => {
    if (!plan || !symbol) return
    const side = plan === "sell" ? "sell" : "buy"
    if (isIndex) {
      toast("warn", { title: `${shortSymbol(symbol)} is an index — not tradable in this account.` })
    } else if (side === "sell" && isLive) {
      toast("warn", { title: "Sell tickets are disabled in live mode." })
    } else {
      setTicketSide(side)
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete("plan")
        return next
      },
      { replace: true },
    )
  }, [isIndex, isLive, plan, setSearchParams, symbol, toast])

  const candlesQuery = useCandles(symbol ?? "", "1d", CHART_CANDLE_LIMIT)
  const candles = useMemo(() => candlesQuery.data ?? [], [candlesQuery.data])

  const tick = symbol ? ltpMap.get(symbol) : undefined
  const lastCandle = candles.length > 0 ? candles[candles.length - 1]! : null
  const prevCandle = candles.length > 1 ? candles[candles.length - 2]! : null
  const liveLtp = tick?.ltp ?? lastCandle?.close ?? 0
  const changePct =
    tick?.change_pct ??
    (lastCandle && prevCandle && prevCandle.close > 0
      ? ((lastCandle.close - prevCandle.close) / prevCandle.close) * 100
      : null)

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
    candles.length > 1
      ? `${fmtDate(candles[0]!.time)} – ${fmtDate(candles[candles.length - 1]!.time)} · ${candles.length} sessions`
      : "—"

  return (
    <section className="view h-full w-full min-w-0">
      {symbol && (
        <ChartHeader
          canNext={nav.next !== null}
          canPrev={nav.prev !== null}
          changePct={changePct}
          index={nav.index}
          isIndex={isIndex}
          listLabel={chartList.label}
          ltp={liveLtp > 0 ? liveLtp : null}
          meta={meta}
          name={listItem?.name ?? meta?.name ?? null}
          onAnalyzeVcp={() => meta && setVisionResult(meta)}
          onExpandSidebar={onExpandSidebar}
          onNext={nav.goNext}
          onPrev={nav.goPrev}
          sidebarCollapsed={sidebarCollapsed}
          symbol={symbol}
          total={nav.total}
        />
      )}

      <div className="relative flex h-full min-h-0 w-full flex-1">
        <div className="chartbox h-full w-full min-w-0 flex-1">
          {!symbol ? (
            <EmptyState className="h-full" icon={CandlestickChartIcon} title="No symbol selected">
              Pick a symbol from the list, the search box, or the Scanner.
            </EmptyState>
          ) : (
            <>
              <TradingChart
                data={candles}
                liveLtp={liveLtp}
                stopLossPrice={stopLossPrice}
                symbol={symbol}
                targetPrice={targetPrice}
                visionOverlay={visionOverlay}
                volumeVisible={true}
              />
              {candlesQuery.isFetching && (
                <div aria-hidden="true" className="absolute inset-x-0 top-10 z-20 h-0.5 animate-pulse bg-accent" />
              )}
              {candlesQuery.isLoading && (
                <div className="pointer-events-none absolute inset-0 top-10 z-10 flex items-center justify-center text-xs text-muted-text">
                  Loading daily candles…
                </div>
              )}
              {candlesQuery.isError && (
                <div className="absolute inset-0 top-10 z-10 flex items-center justify-center bg-background/90">
                  <EmptyState icon={DatabaseZapIcon} title="Candles unavailable" tone="bad">
                    {candlesQuery.error instanceof Error ? candlesQuery.error.message : "The candle API is unavailable."}
                  </EmptyState>
                </div>
              )}
              {candlesQuery.isSuccess && candles.length === 0 && (
                <div className="absolute inset-0 top-10 z-10 flex items-center justify-center bg-background/90">
                  <EmptyState icon={DatabaseZapIcon} title="No daily candles stored">
                    Run the EOD sync from Operations, then retry this symbol.
                  </EmptyState>
                </div>
              )}

              {/* Quick BUY / SELL (opens the ticket panel) */}
              {!isIndex && (
                <div className="absolute right-20 bottom-10 z-20 flex items-center gap-1.5 rounded-lg border border-border bg-surface/90 p-1.5 shadow-xl backdrop-blur-md">
                  <button
                    className={cn(
                      "flex items-center gap-1.5 rounded px-3 py-1.5 font-mono text-xs font-bold transition-colors",
                      ticketSide === "buy"
                        ? "bg-ok text-background ring-2 ring-ok/50"
                        : "bg-ok-soft text-ok hover:bg-ok hover:text-background",
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
                      "flex items-center gap-1.5 rounded px-3 py-1.5 font-mono text-xs font-bold transition-colors",
                      ticketSide === "sell"
                        ? "bg-ko text-white ring-2 ring-ko/50"
                        : "bg-ko-soft text-ko hover:bg-ko hover:text-white",
                      isLive && "cursor-not-allowed opacity-40",
                    )}
                    disabled={isLive}
                    onClick={() => openTicket("sell")}
                    title={isLive ? "Sell disabled in live mode" : `Open SELL ticket for ${shortSymbol(symbol)}`}
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
          <aside className="flex w-[340px] shrink-0 flex-col border-l border-border bg-surface max-[1100px]:absolute max-[1100px]:inset-y-0 max-[1100px]:right-0 max-[1100px]:z-30 max-[1100px]:shadow-2xl">
            <TradeExecutionForm
              currentLtp={liveLtp}
              initialSide={ticketSide}
              key={`${symbol}:${ticketSide}`}
              onClose={clearTicket}
              onTradeConfirmed={() => {
                clearTicket()
                toast("ok", { title: "Trade instruction confirmed", mono: `${shortSymbol(symbol)} · ${mode}` })
              }}
              onValuesChange={(_entry, stop, target) => {
                setStopLossPrice(stop)
                setTargetPrice(target)
              }}
              screeningResultId={meta?.id ?? null}
              symbol={symbol}
            />
          </aside>
        )}
      </div>

      <footer className="cstat">
        <span>{symbol ? `EOD candles · NSE ${isIndex ? "IDX" : "EQ"}` : "Pick a symbol to begin"}</span>
        <span className="r">{rangeText}</span>
      </footer>

      {visionResult && (
        <VcpVisionSheet
          initialAnalysisId={visionResult.vcp_vision?.id ?? null}
          onOpenChange={(open) => {
            if (!open) setVisionResult(null)
          }}
          open
          result={visionResult}
        />
      )}
    </section>
  )
}
