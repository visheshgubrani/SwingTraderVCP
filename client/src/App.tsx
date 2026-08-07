import { useCallback, useEffect, useMemo, useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ConstructionIcon } from "lucide-react"
import { createBrowserRouter, Outlet, RouterProvider } from "react-router"

import { Sidebar } from "@/components/layout/Sidebar"
import { TopBar } from "@/components/layout/TopBar"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { AuthBanner } from "@/features/auth/AuthBanner"
import { FyersCallback } from "@/features/auth/FyersCallback"
import { TradingDashboard } from "@/features/dashboard/TradingDashboard"
import { useTradingAppContext, type ActiveInstrument, type TradingAppContext } from "@/features/dashboard/app-context"
import { FundamentalsView } from "@/features/fundamentals/FundamentalsView"
import { JournalCaptureManager } from "@/features/journal/JournalCaptureManager"
import { JournalView } from "@/features/journal/JournalView"
import { OrderBookTable, type OrderIntentItem } from "@/features/orders/OrderBookTable"
import { DashboardOverview } from "@/features/overview/DashboardOverview"
import { PositionsTable, type PositionItem } from "@/features/positions/PositionsTable"
import { ScannerPage } from "@/features/screener/ScannerPage"
import { TradebookView } from "@/features/tradebook/TradebookView"
import { useOrderIntents, usePositions } from "@/features/trade/api"
import { MarketWSProvider, useMarketData } from "@/lib/MarketWSContext"

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: true } },
})

function UnavailableFeature({ title, description }: { title: string; description: string }) {
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

function TradingAppProviders() {
  return (
    <MarketWSProvider>
      <Outlet />
      <JournalCaptureManager />
    </MarketWSProvider>
  )
}

function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [activeInstrument, setActiveInstrumentState] = useState<ActiveInstrument | null>(null)
  const positionsQuery = usePositions(true)
  const orderIntentsQuery = useOrderIntents()
  const { ltpMap, subscribe, unsubscribe, tickWorkerStatus } = useMarketData()

  const setActiveInstrument = useCallback((instrument: ActiveInstrument | null) => {
    setActiveInstrumentState(instrument)
  }, [])

  const positions: PositionItem[] = useMemo(() => (positionsQuery.data ?? []).map((position) => {
    const averageEntry = position.average_entry_price ? Number(position.average_entry_price) : null
    const currentLtp = ltpMap.get(position.symbol)?.ltp ?? null
    const openQty = position.open_quantity
    let unrealizedPnl: number | null = null
    if (averageEntry !== null && currentLtp !== null && openQty > 0 && (position.state === "open" || position.state === "trailing_active")) {
      unrealizedPnl = position.side === "long" ? (currentLtp - averageEntry) * openQty : (averageEntry - currentLtp) * openQty
    }
    return {
      id: position.id,
      symbol: position.symbol,
      side: position.side,
      quantity: position.quantity,
      open_quantity: position.open_quantity,
      average_entry_price: averageEntry,
      current_ltp: currentLtp,
      current_stop_loss: position.current_stop_loss ? Number(position.current_stop_loss) : null,
      current_target: position.current_target ? Number(position.current_target) : null,
      trailing_rule_desc: position.trailing_rule.type === "none" || !position.trailing_rule.type ? "None" : `${position.trailing_rule.type}: ${position.trailing_rule.value ?? "-"}`,
      realized_pnl: Number(position.realized_pnl),
      unrealized_pnl: unrealizedPnl,
      state: position.state,
      opened_at: position.opened_at,
    }
  }), [ltpMap, positionsQuery.data])

  const orderIntents: OrderIntentItem[] = useMemo(() => (orderIntentsQuery.data ?? []).map((intent) => ({
    id: intent.id,
    idempotency_key: intent.idempotency_key,
    intent_type: intent.intent_type,
    symbol: intent.symbol,
    side: intent.side,
    quantity: intent.quantity,
    order_type: intent.order_type,
    limit_price: intent.limit_price ? Number(intent.limit_price) : undefined,
    status: intent.status,
    execution_mode: intent.execution_mode,
    fyers_async_id: intent.fyers_async_id ?? undefined,
    fyers_order_id: intent.fyers_order_id ?? undefined,
    reason: intent.reason ?? undefined,
    created_at: intent.created_at,
  })), [orderIntentsQuery.data])

  useEffect(() => {
    const symbols = new Set((positionsQuery.data ?? []).map((position) => position.symbol))
    if (activeInstrument?.symbol) symbols.add(activeInstrument.symbol)
    const requested = [...symbols]
    if (!requested.length) return
    subscribe(requested)
    return () => unsubscribe(requested)
  }, [activeInstrument?.symbol, positionsQuery.data, subscribe, unsubscribe])

  const outletContext: TradingAppContext = {
    orderIntents,
    positions,
    rawPositions: positionsQuery.data ?? [],
    setActiveInstrument,
    tickWorkerStatus,
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar activeLtp={activeInstrument?.ltp} activeSymbol={activeInstrument?.symbol} activeTick={activeInstrument?.tick} setSidebarOpen={setSidebarOpen} sidebarOpen={sidebarOpen} />
      <AuthBanner />
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <Sidebar open={sidebarOpen} setOpen={setSidebarOpen} />
        <main className="relative min-w-0 flex-1 overflow-hidden bg-background">
          <Outlet context={outletContext} />
        </main>
      </div>
    </div>
  )
}

function PositionsRoute() {
  const { positions } = useTradingAppContext()
  return <PositionsTable positions={positions} />
}

function OrdersRoute() {
  const { orderIntents } = useTradingAppContext()
  return <OrderBookTable orders={orderIntents} />
}

function OperationsRoute() {
  const { tickWorkerStatus } = useTradingAppContext()
  return <DashboardOverview tickWorkerStatus={tickWorkerStatus} />
}

const router = createBrowserRouter([
  { path: "/callback", Component: FyersCallback },
  {
    Component: TradingAppProviders,
    children: [{
      Component: AppShell,
      children: [
        { index: true, Component: TradingDashboard },
        { path: "scanner", Component: ScannerPage },
        { path: "fundamentals", Component: FundamentalsView },
        { path: "positions", Component: PositionsRoute },
        { path: "orders", Component: OrdersRoute },
        { path: "tradebook", Component: TradebookView },
        { path: "journal", Component: JournalView },
        { path: "operations", Component: OperationsRoute },
        { path: "ledger", Component: () => <UnavailableFeature title="Account ledger is not connected" description="No broker ledger API exists in the current backend, so fabricated balances are intentionally hidden." /> },
        { path: "*", Component: () => <UnavailableFeature title="Page not found" description="Use the trading workstation navigation to return to an available view." /> },
      ],
    }],
  },
])

export default function App() {
  return <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>
}
