import { useCallback, useEffect, useRef, useState } from "react"
import { ConstructionIcon, DatabaseZapIcon, PanelBottomCloseIcon, PanelBottomOpenIcon } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { TradingChart } from "@/features/chart/TradingChart"
import { historicalKeys, useCandles, useSyncStatus } from "@/features/historical/api"
import { OrderBookTable } from "@/features/orders/OrderBookTable"
import { PositionsTable } from "@/features/positions/PositionsTable"
import { ScannerTable } from "@/features/screener/ScannerTable"
import { type ScanResult, useScanResults, useScanRuns } from "@/features/screener/api"
import { useScanWorkflow } from "@/features/screener/useScanWorkflow"
import { TradeExecutionForm } from "@/features/trade/TradeExecutionForm"
import { TradebookView } from "@/features/tradebook/TradebookView"
import { useAuthStatus } from "@/features/auth/api"
import { useMarketData } from "@/lib/MarketWSContext"
import { useTradingAppContext } from "./app-context"

const RATIO_STORAGE_KEY = "swingtrader.dashboard.bottomPanelRatio.v1"
const DEFAULT_BOTTOM_RATIO = 0.32
const MIN_BOTTOM_HEIGHT = 180
const MIN_CHART_HEIGHT = 320
const SPLITTER_HEIGHT = 7
const KEYBOARD_STEP = 24

function loadBottomRatio() {
  const stored = Number(window.localStorage.getItem(RATIO_STORAGE_KEY))
  return Number.isFinite(stored) && stored > 0.1 && stored < 0.8
    ? stored
    : DEFAULT_BOTTOM_RATIO
}

function EmptyChartWorkspace() {
  return (
    <div className="flex h-full items-center justify-center bg-background p-6">
      <Alert className="max-w-lg">
        <DatabaseZapIcon aria-hidden="true" />
        <AlertTitle>Select a scanner result</AlertTitle>
        <AlertDescription>
          Run the EOD scanner or choose a successful run to load its daily chart.
        </AlertDescription>
      </Alert>
    </div>
  )
}

export function TradingDashboard() {
  const { orderIntents, positions, setActiveInstrument } = useTradingAppContext()
  const [bottomTab, setBottomTab] = useState<"scanner" | "positions" | "orders" | "tradebook">("scanner")
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null)
  const [stopLossPrice, setStopLossPrice] = useState(0)
  const [targetPrice, setTargetPrice] = useState(0)
  const [bottomRatio, setBottomRatio] = useState(loadBottomRatio)
  const [bottomHeight, setBottomHeight] = useState(MIN_BOTTOM_HEIGHT)
  const [collapsed, setCollapsed] = useState(false)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const dragStartRef = useRef<{ pointerY: number; height: number } | null>(null)
  const bottomHeightRef = useRef(MIN_BOTTOM_HEIGHT)
  const lastInvalidatedSync = useRef<string | null>(null)

  const queryCache = useQueryClient()
  const authStatus = useAuthStatus()
  const syncStatus = useSyncStatus()
  const scanRuns = useScanRuns()
  const scanWorkflow = useScanWorkflow(authStatus.data)
  const { ltpMap } = useMarketData()
  const activeRun = scanRuns.data?.find((run) => run.id === selectedRunId)
  const scanResults = useScanResults(selectedRunId, activeRun?.status)
  const selectedResult = scanResults.data?.find((result) => result.id === selectedResultId)
  const selectedSymbol = selectedResult?.fyers_symbol ?? ""
  const selectedTick = selectedSymbol ? ltpMap.get(selectedSymbol) : undefined
  const selectedLtp = selectedTick?.ltp ?? selectedResult?.close_price ?? 0
  const candlesQuery = useCandles(selectedSymbol, "1d", 300)

  const clampBottomHeight = useCallback((height: number) => {
    const total = workspaceRef.current?.clientHeight ?? 0
    const maximum = Math.max(MIN_BOTTOM_HEIGHT, total - MIN_CHART_HEIGHT - SPLITTER_HEIGHT)
    return Math.min(Math.max(height, MIN_BOTTOM_HEIGHT), maximum)
  }, [])

  const persistRatio = useCallback((height: number) => {
    const total = workspaceRef.current?.clientHeight ?? 0
    if (!total) return
    const ratio = Math.min(0.8, Math.max(0.1, height / total))
    setBottomRatio(ratio)
    window.localStorage.setItem(RATIO_STORAGE_KEY, String(ratio))
  }, [])

  useEffect(() => {
    const element = workspaceRef.current
    if (!element) return
    const update = () => {
      const next = clampBottomHeight(element.clientHeight * bottomRatio)
      bottomHeightRef.current = next
      setBottomHeight(next)
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [bottomRatio, clampBottomHeight])

  useEffect(() => {
    const runs = scanRuns.data
    if (!runs?.length || selectedRunId) return
    setSelectedRunId(runs.find((run) => run.status === "succeeded")?.id ?? runs[0].id)
  }, [scanRuns.data, selectedRunId])

  useEffect(() => {
    if (!scanWorkflow.scanRunId) return
    setSelectedRunId(scanWorkflow.scanRunId)
    setBottomTab("scanner")
  }, [scanWorkflow.scanRunId])

  useEffect(() => {
    const results = scanResults.data
    if (!results) return
    if (selectedResultId && results.some((result) => result.id === selectedResultId)) return
    setSelectedResultId(results[0]?.id ?? null)
  }, [scanResults.data, selectedResultId])

  useEffect(() => {
    setStopLossPrice(0)
    setTargetPrice(0)
  }, [selectedResult])

  useEffect(() => {
    const completedAt = syncStatus.data?.completed_at
    if (syncStatus.data?.state !== "succeeded" || !completedAt || lastInvalidatedSync.current === completedAt) return
    lastInvalidatedSync.current = completedAt
    void queryCache.invalidateQueries({ queryKey: historicalKeys.candles() })
  }, [queryCache, syncStatus.data])

  useEffect(() => {
    setActiveInstrument(selectedSymbol ? { symbol: selectedSymbol, ltp: selectedLtp, tick: selectedTick } : null)
    return () => setActiveInstrument(null)
  }, [selectedLtp, selectedSymbol, selectedTick, setActiveInstrument])

  const handleSelectResult = (result: ScanResult) => setSelectedResultId(result.id)

  const resizeBy = (delta: number) => {
    setCollapsed(false)
    const next = clampBottomHeight(bottomHeightRef.current + delta)
    bottomHeightRef.current = next
    setBottomHeight(next)
    persistRatio(next)
  }

  return (
    <div className="grid h-full min-h-0 w-full overflow-hidden" ref={workspaceRef} style={{ gridTemplateRows: collapsed ? `minmax(${MIN_CHART_HEIGHT}px, 1fr) ${SPLITTER_HEIGHT}px 0px` : `minmax(${MIN_CHART_HEIGHT}px, 1fr) ${SPLITTER_HEIGHT}px ${bottomHeight}px` }}>
      <section className="flex min-h-0 overflow-hidden">
        <div className="relative h-full min-w-0 flex-1 overflow-hidden">
          {!selectedResult ? <EmptyChartWorkspace /> : (
            <>
              <TradingChart data={candlesQuery.data ?? []} liveLtp={selectedLtp} stopLossPrice={stopLossPrice} symbol={selectedSymbol} targetPrice={targetPrice} />
              {candlesQuery.isLoading && <div className="absolute inset-0 flex items-center justify-center bg-background/80 text-sm text-muted-foreground">Loading daily candles from Postgres…</div>}
              {candlesQuery.isError && <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6"><Alert className="max-w-lg" variant="destructive"><DatabaseZapIcon aria-hidden="true" /><AlertTitle>Could not load candles</AlertTitle><AlertDescription>{candlesQuery.error instanceof Error ? candlesQuery.error.message : "The candle API is unavailable."}</AlertDescription></Alert></div>}
              {candlesQuery.isSuccess && candlesQuery.data.length === 0 && <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6"><Alert className="max-w-lg"><DatabaseZapIcon aria-hidden="true" /><AlertTitle>No daily candles stored</AlertTitle><AlertDescription>Sync the latest EOD data, then retry this result.</AlertDescription></Alert></div>}
            </>
          )}
        </div>
        {selectedResult ? (
          <TradeExecutionForm currentLtp={selectedLtp} onTradeConfirmed={() => { setCollapsed(false); setBottomTab("orders") }} onValuesChange={(_entry, stopLoss, target) => { setStopLossPrice(stopLoss); setTargetPrice(target) }} screeningResultId={selectedResult.id} symbol={selectedSymbol} />
        ) : (
          <aside className="flex h-full w-80 shrink-0 items-center border-l bg-card p-4"><Alert><ConstructionIcon aria-hidden="true" /><AlertTitle>Trade checkpoint unavailable</AlertTitle><AlertDescription>Select a scanner result before creating a traceable trade instruction.</AlertDescription></Alert></aside>
        )}
      </section>

      <div
        aria-label="Resize chart and scanner panels"
        aria-orientation="horizontal"
        aria-valuemax={Math.max(MIN_BOTTOM_HEIGHT, (workspaceRef.current?.clientHeight ?? 0) - MIN_CHART_HEIGHT - SPLITTER_HEIGHT)}
        aria-valuemin={MIN_BOTTOM_HEIGHT}
        aria-valuenow={Math.round(bottomHeight)}
        className="group relative z-10 touch-none cursor-row-resize border-y bg-border/60 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onDoubleClick={() => { setCollapsed(false); setBottomRatio(DEFAULT_BOTTOM_RATIO); window.localStorage.setItem(RATIO_STORAGE_KEY, String(DEFAULT_BOTTOM_RATIO)) }}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp") { event.preventDefault(); resizeBy(KEYBOARD_STEP) }
          if (event.key === "ArrowDown") { event.preventDefault(); resizeBy(-KEYBOARD_STEP) }
          if (event.key === "Home") { event.preventDefault(); resizeBy(-Number.MAX_SAFE_INTEGER) }
          if (event.key === "End") { event.preventDefault(); resizeBy(Number.MAX_SAFE_INTEGER) }
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId)
          dragStartRef.current = { pointerY: event.clientY, height: collapsed ? MIN_BOTTOM_HEIGHT : bottomHeight }
          setCollapsed(false)
        }}
        onPointerMove={(event) => {
          if (!dragStartRef.current || !event.currentTarget.hasPointerCapture(event.pointerId)) return
          const next = clampBottomHeight(dragStartRef.current.height + dragStartRef.current.pointerY - event.clientY)
          bottomHeightRef.current = next
          setBottomHeight(next)
        }}
        onPointerUp={(event) => {
          if (!dragStartRef.current) return
          event.currentTarget.releasePointerCapture(event.pointerId)
          dragStartRef.current = null
          persistRatio(bottomHeightRef.current)
        }}
        role="separator"
        tabIndex={0}
      >
        <span className="absolute left-1/2 top-1/2 h-1 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full bg-muted-foreground/40 transition-colors group-hover:bg-primary" />
        <Button
          aria-label={collapsed ? "Restore lower panel" : "Collapse lower panel"}
          className="absolute right-2 top-1/2 -translate-y-1/2 bg-card"
          onClick={() => setCollapsed((value) => !value)}
          onPointerDown={(event) => event.stopPropagation()}
          size="icon-xs"
          type="button"
          variant="outline"
        >
          {collapsed ? <PanelBottomOpenIcon /> : <PanelBottomCloseIcon />}
        </Button>
      </div>

      <section aria-hidden={collapsed} className="flex min-h-0 flex-col overflow-hidden bg-card">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 font-mono text-xs">
          {([ ["scanner", `SCANNER (${scanResults.data?.length ?? activeRun?.passing_count ?? 0})`], ["positions", `OPEN POSITIONS (${positions.length})`], ["orders", `ORDER INTENTS (${orderIntents.length})`], ["tradebook", "TRADEBOOK"] ] as const).map(([tab, label]) => (
            <button className={bottomTab === tab ? "rounded border bg-accent px-3 py-1 font-semibold text-accent-foreground" : "rounded px-3 py-1 text-muted-foreground hover:text-foreground"} key={tab} onClick={() => setBottomTab(tab)} type="button">{label}</button>
          ))}
          <Button aria-label={collapsed ? "Restore lower panel" : "Collapse lower panel"} className="ml-auto" onClick={() => setCollapsed((value) => !value)} size="icon-xs" type="button" variant="ghost">
            {collapsed ? <PanelBottomOpenIcon /> : <PanelBottomCloseIcon />}
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {bottomTab === "scanner" && <ScannerTable activeRun={activeRun} errorMessage={scanResults.error instanceof Error ? scanResults.error.message : null} isError={scanResults.isError} isLoading={scanResults.isLoading} isRunning={scanWorkflow.isBusy} items={scanResults.data ?? []} onPlanTrade={handleSelectResult} onRunScan={() => void scanWorkflow.start()} onRetry={() => void scanResults.refetch()} onSelectResult={handleSelectResult} onSelectRun={(runId) => { scanWorkflow.reset(); setSelectedRunId(runId); setSelectedResultId(null) }} runs={scanRuns.data ?? []} selectedResultId={selectedResultId} selectedRunId={selectedRunId} workflowError={scanWorkflow.phase === "failed"} workflowMessage={scanWorkflow.message} />}
          {bottomTab === "positions" && <PositionsTable positions={positions} />}
          {bottomTab === "orders" && <OrderBookTable orders={orderIntents} />}
          {bottomTab === "tradebook" && <TradebookView />}
        </div>
      </section>
    </div>
  )
}
