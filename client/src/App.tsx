import { useEffect, useRef, useState } from "react"
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query"
import { ConstructionIcon, DatabaseZapIcon } from "lucide-react"

import { Sidebar, type NavTab } from "@/components/layout/Sidebar"
import { TopBar } from "@/components/layout/TopBar"
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { AuthBanner } from "@/features/auth/AuthBanner"
import { FyersCallback } from "@/features/auth/FyersCallback"
import { useAuthStatus } from "@/features/auth/api"
import { TradingChart } from "@/features/chart/TradingChart"
import {
  historicalKeys,
  useCandles,
  useSyncStatus,
} from "@/features/historical/api"
import { OrderBookTable, type OrderIntentItem } from "@/features/orders/OrderBookTable"
import { DashboardOverview } from "@/features/overview/DashboardOverview"
import { PositionsTable, type PositionItem } from "@/features/positions/PositionsTable"
import { ScannerTable } from "@/features/screener/ScannerTable"
import {
  type ScanResult,
  useScanResults,
  useScanRuns,
} from "@/features/screener/api"
import { useScanWorkflow } from "@/features/screener/useScanWorkflow"
import { TradeExecutionForm } from "@/features/trade/TradeExecutionForm"
import { useOrderIntents, usePositions } from "@/features/trade/api"
import { MarketWSProvider, useMarketData } from "@/lib/MarketWSContext"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true,
    },
  },
})

function UnavailableFeature({
  title,
  description,
}: {
  title: string
  description: string
}) {
  return (
    <div className="flex h-full items-center justify-center bg-background p-6">
      <Alert className="max-w-xl">
        <ConstructionIcon aria-hidden="true" />
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>{description}</AlertDescription>
      </Alert>
    </div>
  )
}

function EmptyChartWorkspace() {
  return (
    <div className="flex h-full items-center justify-center bg-background p-6">
      <Alert className="max-w-lg">
        <DatabaseZapIcon aria-hidden="true" />
        <AlertTitle>Select a real scanner result</AlertTitle>
        <AlertDescription>
          Run the EOD scanner or choose a successful run from the shortlist
          history. Its Fyers symbol will load 300 daily candles from Postgres.
        </AlertDescription>
      </Alert>
    </div>
  )
}

function AppContent() {
  const [activeTab, setActiveTab] = useState<NavTab>("chart")
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [bottomTab, setBottomTab] = useState<
    "scanner" | "positions" | "orders" | "tradebook"
  >("scanner")
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedResultId, setSelectedResultId] = useState<string | null>(
    null,
  )
  const [stopLossPrice, setStopLossPrice] = useState(0)
  const [targetPrice, setTargetPrice] = useState(0)
  const lastInvalidatedSync = useRef<string | null>(null)

  const queryCache = useQueryClient()
  const authStatus = useAuthStatus()
  const syncStatus = useSyncStatus()
  const scanRuns = useScanRuns()
  const scanWorkflow = useScanWorkflow(authStatus.data)
  const positionsQuery = usePositions(true)
  const orderIntentsQuery = useOrderIntents()
  const {
    ltpMap,
    subscribe,
    unsubscribe,
    tickWorkerStatus,
  } = useMarketData()

  const activeRun = scanRuns.data?.find((run) => run.id === selectedRunId)
  const scanResults = useScanResults(selectedRunId, activeRun?.status)
  const selectedResult = scanResults.data?.find(
    (result) => result.id === selectedResultId,
  )
  const selectedSymbol = selectedResult?.fyers_symbol ?? ""
  const selectedTick = selectedSymbol ? ltpMap.get(selectedSymbol) : undefined
  const selectedLtp =
    selectedTick?.ltp ?? selectedResult?.close_price ?? 0
  const candlesQuery = useCandles(selectedSymbol, "1d", 300)

  const positions: PositionItem[] = (positionsQuery.data ?? []).map(
    (position) => {
      const averageEntry = position.average_entry_price
        ? Number(position.average_entry_price)
        : null
      const currentLtp = ltpMap.get(position.symbol)?.ltp ?? null
      const openQty = position.open_quantity
      let unrealizedPnl: number | null = null
      if (
        averageEntry !== null &&
        currentLtp !== null &&
        openQty > 0 &&
        (position.state === "open" || position.state === "trailing_active")
      ) {
        unrealizedPnl =
          position.side === "long"
            ? (currentLtp - averageEntry) * openQty
            : (averageEntry - currentLtp) * openQty
      }

      return {
      id: position.id,
      symbol: position.symbol,
      side: position.side,
      quantity: position.quantity,
      open_quantity: position.open_quantity,
      average_entry_price: averageEntry,
      current_ltp: currentLtp,
      current_stop_loss: position.current_stop_loss
        ? Number(position.current_stop_loss)
        : null,
      current_target: position.current_target
        ? Number(position.current_target)
        : null,
      trailing_rule_desc:
        position.trailing_rule.type === "none" ||
        !position.trailing_rule.type
          ? "None"
          : `${position.trailing_rule.type}: ${
              position.trailing_rule.value ?? "-"
            }`,
      realized_pnl: Number(position.realized_pnl),
      unrealized_pnl: unrealizedPnl,
      state: position.state,
      opened_at: position.opened_at,
    }
    },
  )

  const orderIntents: OrderIntentItem[] = (
    orderIntentsQuery.data ?? []
  ).map((intent) => ({
    id: intent.id,
    idempotency_key: intent.idempotency_key,
    intent_type: intent.intent_type,
    symbol: intent.symbol,
    side: intent.side,
    quantity: intent.quantity,
    order_type: intent.order_type,
    limit_price: intent.limit_price
      ? Number(intent.limit_price)
      : undefined,
    status: intent.status,
    execution_mode: intent.execution_mode,
    fyers_async_id: intent.fyers_async_id ?? undefined,
    fyers_order_id: intent.fyers_order_id ?? undefined,
    reason: intent.reason ?? undefined,
    created_at: intent.created_at,
  }))

  useEffect(() => {
    const runs = scanRuns.data
    if (!runs?.length || selectedRunId) return
    setSelectedRunId(
      runs.find((run) => run.status === "succeeded")?.id ?? runs[0].id,
    )
  }, [scanRuns.data, selectedRunId])

  useEffect(() => {
    if (!scanWorkflow.scanRunId) return
    setSelectedRunId(scanWorkflow.scanRunId)
    setBottomTab("scanner")
  }, [scanWorkflow.scanRunId])

  useEffect(() => {
    const results = scanResults.data
    if (!results) return
    if (
      selectedResultId &&
      results.some((result) => result.id === selectedResultId)
    ) {
      return
    }
    setSelectedResultId(results[0]?.id ?? null)
  }, [scanResults.data, selectedResultId])

  useEffect(() => {
    if (!selectedResult) {
      setStopLossPrice(0)
      setTargetPrice(0)
      return
    }
    setStopLossPrice(0)
    setTargetPrice(0)
  }, [selectedResult])

  useEffect(() => {
    const completedAt = syncStatus.data?.completed_at
    if (
      syncStatus.data?.state !== "succeeded" ||
      !completedAt ||
      lastInvalidatedSync.current === completedAt
    ) {
      return
    }
    lastInvalidatedSync.current = completedAt
    void queryCache.invalidateQueries({
      queryKey: historicalKeys.candles(),
    })
  }, [queryCache, syncStatus.data])

  useEffect(() => {
    const symbols = new Set(
      (positionsQuery.data ?? []).map((position) => position.symbol),
    )
    if (selectedSymbol) symbols.add(selectedSymbol)
    const requested = [...symbols]
    if (!requested.length) return
    subscribe(requested)
    return () => unsubscribe(requested)
  }, [positionsQuery.data, selectedSymbol, subscribe, unsubscribe])

  const handleSelectResult = (result: ScanResult) => {
    setSelectedResultId(result.id)
  }

  const handleChartValuesChange = (
    _entry: number,
    stopLoss: number,
    target: number,
  ) => {
    setStopLossPrice(stopLoss)
    setTargetPrice(target)
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar
        activeLtp={selectedLtp}
        activeSymbol={selectedSymbol}
        activeTick={selectedTick}
        setSidebarOpen={setSidebarOpen}
        sidebarOpen={sidebarOpen}
      />
      <AuthBanner />

      <div className="relative flex flex-1 overflow-hidden">
        <Sidebar
          activeTab={activeTab}
          open={sidebarOpen}
          setActiveTab={setActiveTab}
          setOpen={setSidebarOpen}
        />

        <main className="relative flex flex-1 flex-col overflow-hidden bg-background">
          {activeTab === "overview" && (
            <DashboardOverview tickWorkerStatus={tickWorkerStatus} />
          )}

          {activeTab === "chart" && (
            <div className="flex h-full w-full flex-col overflow-hidden">
              <div className="flex flex-1 overflow-hidden border-b">
                <div className="relative h-full flex-1 overflow-hidden">
                  {!selectedResult ? (
                    <EmptyChartWorkspace />
                  ) : (
                    <>
                      <TradingChart
                        data={candlesQuery.data ?? []}
                        liveLtp={selectedLtp}
                        stopLossPrice={stopLossPrice}
                        symbol={selectedSymbol}
                        targetPrice={targetPrice}
                      />
                      {candlesQuery.isLoading && (
                        <div className="absolute inset-0 flex items-center justify-center bg-background/80 text-sm text-muted-foreground">
                          Loading daily candles from Postgres…
                        </div>
                      )}
                      {candlesQuery.isError && (
                        <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6">
                          <Alert className="max-w-lg" variant="destructive">
                            <DatabaseZapIcon aria-hidden="true" />
                            <AlertTitle>Could not load candles</AlertTitle>
                            <AlertDescription>
                              {candlesQuery.error instanceof Error
                                ? candlesQuery.error.message
                                : "The candle API is unavailable."}
                            </AlertDescription>
                          </Alert>
                        </div>
                      )}
                      {candlesQuery.isSuccess &&
                        candlesQuery.data.length === 0 && (
                          <div className="absolute inset-0 flex items-center justify-center bg-background/90 p-6">
                            <Alert className="max-w-lg">
                              <DatabaseZapIcon aria-hidden="true" />
                              <AlertTitle>No daily candles stored</AlertTitle>
                              <AlertDescription>
                                Sync the latest EOD data, then retry this
                                scanner result.
                              </AlertDescription>
                            </Alert>
                          </div>
                        )}
                    </>
                  )}
                </div>

                {selectedResult ? (
                  <TradeExecutionForm
                    currentLtp={selectedLtp}
                    onTradeConfirmed={() => setBottomTab("orders")}
                    onValuesChange={handleChartValuesChange}
                    screeningResultId={selectedResult.id}
                    symbol={selectedSymbol}
                  />
                ) : (
                  <aside className="flex h-full w-80 shrink-0 items-center border-l bg-card p-4">
                    <Alert>
                      <ConstructionIcon aria-hidden="true" />
                      <AlertTitle>Trade checkpoint unavailable</AlertTitle>
                      <AlertDescription>
                        Select a scanner result before creating a traceable
                        trade instruction.
                      </AlertDescription>
                    </Alert>
                  </aside>
                )}
              </div>

              <div className="flex h-72 shrink-0 flex-col bg-card">
                <div className="flex h-9 items-center gap-2 border-b px-3 font-mono text-xs">
                  {(
                    [
                      [
                        "scanner",
                        `SCANNER (${
                          scanResults.data?.length ??
                          activeRun?.passing_count ??
                          0
                        })`,
                      ],
                      ["positions", `OPEN POSITIONS (${positions.length})`],
                      ["orders", `ORDER INTENTS (${orderIntents.length})`],
                      ["tradebook", "TRADEBOOK (P6+)"],
                    ] as const
                  ).map(([tab, label]) => (
                    <button
                      className={
                        bottomTab === tab
                          ? "rounded border bg-accent px-3 py-1 font-semibold text-accent-foreground"
                          : "rounded px-3 py-1 text-muted-foreground hover:text-foreground"
                      }
                      key={tab}
                      onClick={() => setBottomTab(tab)}
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <div className="flex-1 overflow-hidden">
                  {bottomTab === "scanner" && (
                    <ScannerTable
                      activeRun={activeRun}
                      errorMessage={
                        scanResults.error instanceof Error
                          ? scanResults.error.message
                          : null
                      }
                      isError={scanResults.isError}
                      isLoading={scanResults.isLoading}
                      isRunning={scanWorkflow.isBusy}
                      items={scanResults.data ?? []}
                      onPlanTrade={handleSelectResult}
                      onRunScan={() => void scanWorkflow.start()}
                      onRetry={() => void scanResults.refetch()}
                      onSelectResult={handleSelectResult}
                      onSelectRun={(runId) => {
                        scanWorkflow.reset()
                        setSelectedRunId(runId)
                        setSelectedResultId(null)
                      }}
                      runs={scanRuns.data ?? []}
                      selectedResultId={selectedResultId}
                      selectedRunId={selectedRunId}
                      workflowError={scanWorkflow.phase === "failed"}
                      workflowMessage={scanWorkflow.message}
                    />
                  )}
                  {bottomTab === "positions" && (
                    <PositionsTable positions={positions} />
                  )}
                  {bottomTab === "orders" && (
                    <OrderBookTable orders={orderIntents} />
                  )}
                  {bottomTab === "tradebook" && (
                    <UnavailableFeature
                      description="Closed-trade reconciliation and journal data arrive in later phases. No sample trades are shown."
                      title="Tradebook is not connected yet"
                    />
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === "positions" && (
            <PositionsTable positions={positions} />
          )}
          {activeTab === "orders" && (
            <OrderBookTable orders={orderIntents} />
          )}
          {activeTab === "tradebook" && (
            <UnavailableFeature
              description="P6 reconciliation must establish authoritative closed-trade records before this view is connected."
              title="Tradebook planned after reconciliation"
            />
          )}
          {activeTab === "journal" && (
            <UnavailableFeature
              description="The read-only journal and AI coach belong to P8 and remain outside this P0–P4 API integration."
              title="Journal and AI coach are not implemented"
            />
          )}
          {activeTab === "ledger" && (
            <UnavailableFeature
              description="No broker ledger API exists in the current backend, so fabricated balances are intentionally hidden."
              title="Account ledger is not connected"
            />
          )}
        </main>
      </div>
    </div>
  )
}

export default function App() {
  const isFyersCallback = window.location.pathname === "/callback"

  return (
    <QueryClientProvider client={queryClient}>
      {isFyersCallback ? (
        <FyersCallback />
      ) : (
        <MarketWSProvider>
          <AppContent />
        </MarketWSProvider>
      )}
    </QueryClientProvider>
  )
}
