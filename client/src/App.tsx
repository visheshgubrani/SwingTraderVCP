import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ConstructionIcon } from "lucide-react"
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation, useNavigate, useSearchParams } from "react-router"

import { StageNav } from "@/components/layout/StageNav"
import { TerminalTopBar } from "@/components/layout/TerminalTopBar"
import { ToastProvider } from "@/components/terminal/toast"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AuthBanner } from "@/features/auth/AuthBanner"
import { AuthProvider, useAppAuth } from "@/features/auth/AuthContext"
import { FyersCallback } from "@/features/auth/FyersCallback"
import { LoginPage } from "@/features/auth/LoginPage"
import { CHART_LIST_PARAMS } from "@/features/chart/chartList"
import { ChartPage } from "@/features/chart/ChartWorkspace"
import {
  TradingAppProvider,
  useTradingAppContext,
  type ChartContextParams,
  type TradingAppContext,
} from "@/features/dashboard/app-context"
import { FundamentalsView } from "@/features/fundamentals/FundamentalsView"
import { JournalCaptureManager } from "@/features/journal/JournalCaptureManager"
import { JournalView } from "@/features/journal/JournalView"
import { OrderBookTable, type OrderIntentItem } from "@/features/orders/OrderBookTable"
import { DashboardOverview } from "@/features/overview/DashboardOverview"
import { PositionsTable, type PositionItem } from "@/features/positions/PositionsTable"
import { AttemptDetailPage } from "@/features/proposals/AttemptDetail"
import { ProposalDetailPage } from "@/features/proposals/ProposalDetail"
import { ProposalsHub } from "@/features/proposals/ProposalsHub"
import { ScannerPage } from "@/features/screener/ScannerPage"
import { TradebookView } from "@/features/tradebook/TradebookView"
import { useOrderIntents, usePositions } from "@/features/trade/api"
import { useWatchlistItems } from "@/features/watchlist/api"
import { MarketWSProvider, useMarketData } from "@/lib/MarketWSContext"
import { NIFTY50_INDEX } from "@/lib/marketSymbols"

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

function AuthGate({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAppAuth()

  if (isLoading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-background text-foreground">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <p className="font-mono text-xs text-muted-foreground">Authenticating workstation session...</p>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <LoginPage />
  }

  return <>{children}</>
}

function TradingAppProviders() {
  return (
    <AuthGate>
      <ToastProvider>
        <MarketWSProvider>
          <Outlet />
          <JournalCaptureManager />
        </MarketWSProvider>
      </ToastProvider>
    </AuthGate>
  )
}

const LAST_SEARCH_KEY = "vcp.nav.lastSearch"

/** Top-level module path for a location ("/proposals/abc" → null: detail routes are not remembered). */
function moduleOf(pathname: string): string | null {
  return /^\/[a-z]*$/.test(pathname) ? pathname : null
}

function readLastSearches(): Record<string, string> {
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(LAST_SEARCH_KEY) ?? "{}") as unknown
    return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {}
  } catch {
    return {}
  }
}

/** Terminal frame: top bar → banners → stage (module view + bottom module tabs). */
function AppShell() {
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const positionsQuery = usePositions(true)
  const orderIntentsQuery = useOrderIntents()
  const watchlistItemsQuery = useWatchlistItems()
  const { ltpMap, subscribe, unsubscribe, tickWorkerStatus } = useMarketData()
  const subscribedRef = useRef<Set<string>>(new Set())

  const onChart = location.pathname === "/"
  const chartSymbol = onChart ? searchParams.get("symbol") : null

  // Remember each module's last query string (chart symbol + list, scanner
  // filters, proposal tab …) so the bottom tabs return to where you left off.
  const [lastSearches, setLastSearches] = useState<Record<string, string>>(() => readLastSearches())
  useEffect(() => {
    const module = moduleOf(location.pathname)
    if (module === null) return
    // A detail drawer is a transient step, not where the tab should reopen.
    const params = new URLSearchParams(location.search)
    params.delete("proposal")
    params.delete("attempt")
    const search = params.size > 0 ? `?${params.toString()}` : ""
    setLastSearches((prev) => {
      if (prev[module] === search) return prev
      const next = { ...prev, [module]: search }
      try {
        window.sessionStorage.setItem(LAST_SEARCH_KEY, JSON.stringify(next))
      } catch {
        // Session storage unavailable — the in-memory value still works.
      }
      return next
    })
  }, [location.pathname, location.search])
  const lastChartSearch = lastSearches["/"] ?? ""

  const openChart = useCallback(
    (fyersSymbol: string, context?: ChartContextParams) => {
      const params = new URLSearchParams({ symbol: fyersSymbol })
      if (context) {
        for (const [key, value] of Object.entries(context)) if (value) params.set(key, value)
      } else {
        const last = new URLSearchParams(lastChartSearch)
        for (const key of CHART_LIST_PARAMS) {
          const value = last.get(key)
          if (value) params.set(key, value)
        }
      }
      navigate(`/?${params.toString()}`)
    },
    [lastChartSearch, navigate],
  )

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

  // Keep the WS subscription set in sync: open positions ∪ chart symbol ∪
  // active watchlist ∪ benchmark index.
  useEffect(() => {
    const watchlistSymbols = (watchlistItemsQuery.data ?? []).map((item) => item.fyers_symbol)
    const symbols = new Set<string>([NIFTY50_INDEX])
    for (const position of positionsQuery.data ?? []) symbols.add(position.symbol)
    if (chartSymbol) symbols.add(chartSymbol)
    for (const symbol of watchlistSymbols) symbols.add(symbol)

    const added = [...symbols].filter((symbol) => !subscribedRef.current.has(symbol))
    const dropped = [...subscribedRef.current].filter((symbol) => !symbols.has(symbol))
    if (added.length) subscribe(added)
    if (dropped.length) unsubscribe(dropped)
    dropped.forEach((symbol) => subscribedRef.current.delete(symbol))
    added.forEach((symbol) => subscribedRef.current.add(symbol))
  }, [chartSymbol, positionsQuery.data, subscribe, unsubscribe, watchlistItemsQuery.data])

  const outletContext: TradingAppContext = {
    orderIntents,
    positions,
    rawPositions: positionsQuery.data ?? [],
    tickWorkerStatus,
    chartSymbol,
    openChart,
  }

  return (
    <div className="app">
      <TradingAppProvider value={outletContext}>
        <TerminalTopBar />
        <AuthBanner />
        <div className="main max-[720px]:overflow-y-auto">
          <div className="stage max-[720px]:min-h-[640px]">
            <div className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
              <Outlet />
            </div>
            <StageNav
              lastSearches={lastSearches}
              orderIntentsCount={orderIntents.length}
              positionsCount={positions.length}
            />
          </div>
        </div>
      </TradingAppProvider>
    </div>
  )
}

function PositionsRoute() {
  const { positions, openChart } = useTradingAppContext()
  return <PositionsTable onOpenSymbol={openChart} positions={positions} />
}

function OrdersRoute() {
  const { orderIntents, openChart } = useTradingAppContext()
  return <OrderBookTable onOpenSymbol={openChart} orders={orderIntents} />
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
        { index: true, Component: ChartPage },
        { path: "scanner", Component: ScannerPage },
        { path: "proposals", Component: ProposalsHub },
        { path: "proposals/:proposalId", Component: ProposalDetailPage },
        { path: "proposals/attempts/:attemptId", Component: AttemptDetailPage },
        { path: "fundamentals", Component: FundamentalsView },
        { path: "positions", Component: PositionsRoute },
        { path: "orders", Component: OrdersRoute },
        { path: "tradebook", Component: TradebookView },
        { path: "journal", Component: JournalView },
        { path: "operations", Component: OperationsRoute },
        { path: "ledger", element: <Navigate replace to="/proposals?tab=ledger" /> },
        { path: "*", Component: () => <UnavailableFeature title="Page not found" description="Use the trading workstation navigation to return to an available view." /> },
      ],
    }],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <TooltipProvider>
          <RouterProvider router={router} />
        </TooltipProvider>
      </AuthProvider>
    </QueryClientProvider>
  )
}
