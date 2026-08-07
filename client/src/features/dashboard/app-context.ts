import { useOutletContext } from "react-router"

import type { OrderIntentItem } from "@/features/orders/OrderBookTable"
import type { PositionItem } from "@/features/positions/PositionsTable"
import type { Position } from "@/features/trade/api"
import type { TickData, TickWorkerStatus } from "@/lib/MarketWSContext"

export interface ActiveInstrument {
  ltp: number
  symbol: string
  tick?: TickData
}

export interface TradingAppContext {
  orderIntents: OrderIntentItem[]
  positions: PositionItem[]
  rawPositions: Position[]
  setActiveInstrument: (instrument: ActiveInstrument | null) => void
  tickWorkerStatus: TickWorkerStatus | null
}

export function useTradingAppContext() {
  return useOutletContext<TradingAppContext>()
}
