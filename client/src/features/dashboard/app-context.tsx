import { createContext, useContext, type ReactNode } from "react"

import type { OrderIntentItem } from "@/features/orders/OrderBookTable"
import type { PositionItem } from "@/features/positions/PositionsTable"
import type { Position } from "@/features/trade/api"
import type { TickWorkerStatus } from "@/lib/MarketWSContext"

export interface TradingAppContext {
  orderIntents: OrderIntentItem[]
  positions: PositionItem[]
  rawPositions: Position[]
  tickWorkerStatus: TickWorkerStatus | null
  /** Fyers symbol currently on the chart (derived from the /?symbol= route). */
  chartSymbol: string | null
  /** Open a symbol on the chart workspace (navigates to /?symbol=…). */
  openChart: (fyersSymbol: string) => void
}

const TradingAppContextValue = createContext<TradingAppContext | null>(null)

export function TradingAppProvider({
  value,
  children,
}: {
  value: TradingAppContext
  children: ReactNode
}) {
  return <TradingAppContextValue.Provider value={value}>{children}</TradingAppContextValue.Provider>
}

export function useTradingAppContext() {
  const ctx = useContext(TradingAppContextValue)
  if (!ctx) throw new Error("useTradingAppContext must be used inside <TradingAppProvider>")
  return ctx
}
