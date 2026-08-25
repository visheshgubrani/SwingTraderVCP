import { describe, it, expect } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { OrderBookTable, type OrderIntentItem } from "../OrderBookTable"

describe("OrderBookTable Component", () => {
  const mockOrders: OrderIntentItem[] = [
    {
      id: "ord-1",
      idempotency_key: "intent-1-entry",
      intent_type: "entry",
      symbol: "NSE:TITAN-EQ",
      side: "buy",
      quantity: 100,
      order_type: "market",
      status: "filled",
      execution_mode: "paper",
      fyers_order_id: "paper-ord-1",
      created_at: "2026-08-25T09:35:00Z",
    },
    {
      id: "ord-2",
      idempotency_key: "intent-2-target-1",
      intent_type: "target_exit",
      symbol: "NSE:TITAN-EQ",
      side: "sell",
      quantity: 25,
      order_type: "market",
      status: "submitted",
      execution_mode: "paper",
      created_at: "2026-08-25T11:00:00Z",
    },
    {
      id: "ord-3",
      idempotency_key: "intent-3-sl",
      intent_type: "stop_loss",
      symbol: "NSE:INFY-EQ",
      side: "sell",
      quantity: 50,
      order_type: "market",
      status: "rejected",
      execution_mode: "live",
      reason: "Kill switch engaged",
      created_at: "2026-08-25T12:00:00Z",
    },
  ]

  it("renders order book header with intents count", () => {
    renderWithProviders(<OrderBookTable orders={mockOrders} />)

    expect(screen.getByText("TODAY ORDER BOOK")).toBeInTheDocument()
    expect(screen.getByText("3 INTENTS LOGGED")).toBeInTheDocument()
    expect(screen.getByText("INTENT TYPE")).toBeInTheDocument()
    expect(screen.getByText("LIMIT PRICE")).toBeInTheDocument()
  })

  it("renders orders with status badges and execution modes", () => {
    renderWithProviders(<OrderBookTable orders={mockOrders} />)

    expect(screen.getAllByText("NSE:TITAN-EQ").length).toBe(2)
    expect(screen.getByText("NSE:INFY-EQ")).toBeInTheDocument()

    expect(screen.getByText("paper-ord-1")).toBeInTheDocument()
    expect(screen.getAllByText("paper").length).toBe(2)
    expect(screen.getByText("live")).toBeInTheDocument()
  })
})
