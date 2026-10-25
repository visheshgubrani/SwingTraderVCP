import { createContext, useContext, type ReactNode } from "react"

import type { OrderIntentItem } from "@/features/orders/OrderBookTable"
import type { PositionItem } from "@/features/positions/PositionsTable"
import type { Position } from "@/features/trade/api"
import type { TickWorkerStatus } from "@/lib/MarketWSContext"

/** Chart list context carried in the chart URL (see features/chart/chartList.ts). */
export type ChartContextParams = Partial<Record<"list" | "run" | "q" | "grade" | "sort" | "dir", string>>

export interface TradingAppContext {
  orderIntents: OrderIntentItem[]
  positions: PositionItem[]
  rawPositions: Position[]
  tickWorkerStatus: TickWorkerStatus | null
  /** Fyers symbol currently on the chart (the `symbol` param of the chart route). */
  chartSymbol: string | null
  /**
   * Open a symbol on the chart. With `context`, the chart sidebar shows that
   * list (e.g. the scanner's run + filters); without it the last chart list is kept.
   */
  openChart: (fyersSymbol: string, context?: ChartContextParams) => void
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
