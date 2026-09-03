import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import { DatabaseZapIcon } from "lucide-react"

import { useToast } from "@/components/terminal/toast"
import { Seg } from "@/components/terminal/bits"
import { TradingChart } from "@/features/chart/TradingChart"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { historicalKeys, useCandles, useSyncStatus } from "@/features/historical/api"
import { defaultScanRunId, useScanResults, useScanRuns, type ScanResult } from "@/features/screener/api"
import { useVcpVisionAnalysis } from "@/features/screener/vcpVision"
import { useExecutionStatus } from "@/features/trade/api"
import { TradeExecutionForm } from "@/features/trade/TradeExecutionForm"
import { useMarketData } from "@/lib/MarketWSContext"
import { shortSymbol } from "@/lib/marketSymbols"
import { fmtNum, fmtPct, toneCls } from "@/lib/format"
import { cn } from "@/lib/utils"

type Timeframe = "1M" | "3M" | "6M" | "1Y" | "2Y"
type TicketSide = "buy" | "sell" | null

const TIMEFRAMES: Timeframe[] = ["1M", "3M", "6M", "1Y", "2Y"]
const TF_SESSIONS: Record<Timeframe, number> = { "1M": 22, "3M": 64, "6M": 127, "1Y": 253, "2Y": 516 }
const SMA_COLORS: Record<number, string> = { 20: "#38bdf8", 50: "#f59e0b", 200: "#22c55e" }

function EmptyChart({ text }: { text: string }) {
  return (
    <div className="grid h-full place-content-center gap-2 bg-background p-6 text-center">
      <DatabaseZapIcon aria-hidden="true" className="mx-auto h-5 w-5 text-muted-text" />
      <p className="max-w-md text-xs text-muted-foreground">{text}</p>
    </div>
  )
}

/** Chart workspace (design chart view): quote bar + toolbar + chart + ticket. */
export function ChartWorkspace() {
  const { chartSymbol } = useTradingAppContext()
  const { ltpMap } = useMarketData()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryCache = useQueryClient()
  const syncStatus = useSyncStatus()
  const execution = useExecutionStatus()

  const [tf, setTf] = useState<Timeframe>("6M")
  const [smaOn, setSmaOn] = useState<Record<number, boolean>>({ 20: false, 50: true, 200: true })
  const [volOn, setVolOn] = useState(true)
  const [ticketSide, setTicketSide] = useState<TicketSide>(null)
  const [stopLossPrice, setStopLossPrice] = useState(0)
  const [targetPrice, setTargetPrice] = useState(0)
  const [defaulted, setDefaulted] = useState(false)
  const lastInvalidatedSync = useRef<string | null>(null)

  // Scanner meta for the current symbol (quote chips + traceable ticket).
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

  const candlesQuery = useCandles(symbol ?? "", "1d", 520)
  const candles = candlesQuery.data ?? []
  const viewBars = useMemo(() => candles.slice(-TF_SESSIONS[tf]), [candles, tf])

  const tick = symbol ? ltpMap.get(symbol) : undefined
  const lastCandle = candles.length > 0 ? candles[candles.length - 1]! : null
  const liveLtp = tick?.ltp ?? lastCandle?.close ?? 0
  const changeAbs = tick?.change ?? (lastCandle ? lastCandle.close - lastCandle.open : null)
  const change =
    tick?.change_pct ??
    (lastCandle && lastCandle.open > 0 ? ((lastCandle.close - lastCandle.open) / lastCandle.open) * 100 : null)
  const tone = toneCls(change)

  const ohlc = useMemo(() => {
    const open = tick?.open ?? lastCandle?.open ?? null
    const high = tick?.high ?? lastCandle?.high ?? null
    const low = tick?.low ?? lastCandle?.low ?? null
    return [
      { label: "O", value: open },
      { label: "H", value: high },
      { label: "L", value: low },
      { label: "C", value: liveLtp > 0 ? liveLtp : null },
    ]
  }, [lastCandle, liveLtp, tick?.high, tick?.low, tick?.open])

  const high52 = useMemo(() => {
    const windowBars = candles.slice(-260)
    return windowBars.length > 0 ? Math.max(...windowBars.map((bar) => bar.high)) : null
  }, [candles])

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

  const smaOverlays = useMemo(
    () =>
      [20, 50, 200]
        .filter((period) => smaOn[period])
        .map((period) => ({ period, color: SMA_COLORS[period]! })),
    [smaOn],
  )

  const rangeText =
    viewBars.length > 1
      ? `${fmtRange(viewBars[0]!.time, viewBars[viewBars.length - 1]!.time)} · ${viewBars.length} sessions`
      : "—"

  const mode = execution.data?.execution_mode.toUpperCase() ?? "PAPER"

  return (
    <section className="view">
      {/* Quote bar */}
      <div className="qbar">
        <div className="qsym">
          <div className="st">
            <h2>{symbol ? shortSymbol(symbol) : "—"}</h2>
            <span className="ex">{symbol ? `NSE · ${isIndex ? "IDX" : "EQ"}` : ""}</span>
          </div>
          <div className="nm">{meta?.name ?? symbol ?? "Select a symbol"}</div>
        </div>

        <div className="qpx">
          <span className={cn("ltp", tone)}>{fmtNum(liveLtp || null)}</span>
          <span className={cn("chg", tone)}>
            {changeAbs !== null ? `${changeAbs >= 0 ? "+" : ""}${fmtNum(changeAbs)}` : "—"}
            <br />
            {fmtPct(change)}
          </span>
        </div>

        <div className="qmini max-[1720px]:hidden">
          {ohlc.map((cell) => (
            <div className="mcell" key={cell.label}>
              <span className="l">{cell.label}</span>
              <span className={cn("v", cell.label === "C" && tone)}>{fmtNum(cell.value)}</span>
            </div>
          ))}
        </div>

        <div className="qchips max-[1100px]:hidden">
          {meta?.technical_score != null && (
            <span className="qchip sc">
              {meta.score_grade ?? "·"} · {fmtNum(meta.technical_score, 2)}
            </span>
          )}
          {meta?.rs_rating != null && <span className="qchip">RS {meta.rs_rating}</span>}
          {high52 !== null && <span className="qchip w52 max-[1720px]:hidden">52W HI {fmtNum(high52)}</span>}
          {isIndex && (
            <>
              <span className="qchip">INDEX</span>
              <span className="qchip">VIEW ONLY</span>
            </>
          )}
        </div>

        <div className="qside">
          <Seg
            aria-label="Open order ticket"
            className="seg-side"
            onValueChange={openTicket}
            options={[
              {
                value: "buy",
                label: "BUY",
                title: symbol ? "Open buy order ticket" : "Select a symbol first",
                disabled: !symbol,
              },
              {
                value: "sell",
                label: "SELL",
                title: execution.data?.execution_mode === "live" ? "Sell disabled in live mode" : "Open sell order ticket",
                disabled: !symbol || execution.data?.execution_mode === "live",
              },
            ]}
            side={ticketSide}
            value={ticketSide ?? "buy"}
          />
        </div>
      </div>

      {/* Chart toolbar */}
      <div className="ctool">
        <div className="grp" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((value) => (
            <button
              aria-pressed={tf === value}
              className={cn("tf", tf === value && "on")}
              key={value}
              onClick={() => setTf(value)}
              type="button"
            >
              {value}
            </button>
          ))}
        </div>
        <span className="csep" />
        <div className="grp" role="group" aria-label="Moving averages and volume">
          {[20, 50, 200].map((period) => (
            <button
              aria-pressed={smaOn[period] ?? false}
              className={cn("smatg", `c${period}`, smaOn[period] && "on")}
              key={period}
              onClick={() => setSmaOn((prev) => ({ ...prev, [period]: !(prev[period] ?? false) }))}
              type="button"
            >
              <i aria-hidden="true" />
              SMA {period}
            </button>
          ))}
        </div>
        <span className="csep" />
        <button
          aria-pressed={volOn}
          className={cn("smatg", volOn && "on")}
          onClick={() => setVolOn((value) => !value)}
          type="button"
        >
          <i aria-hidden="true" />
          VOL
        </button>
        <span className="cmeta">DAILY · NSE {symbol ? (isIndex ? "IDX" : "EQ") : "—"}</span>
      </div>

      {/* Chart + optional ticket panel */}
      <div className="flex min-h-0 flex-1">
        <div className="chartbox min-w-0 flex-1">
          {!symbol ? (
            <EmptyChart text="Pick a symbol from the watchlist, the search box, or the scanner scoreboard." />
          ) : (
            <>
              <TradingChart
                data={viewBars}
                liveLtp={liveLtp}
                smaOverlays={smaOverlays}
                stopLossPrice={stopLossPrice}
                symbol={symbol}
                targetPrice={targetPrice}
                visionOverlay={visionOverlay}
                volumeVisible={volOn}
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
