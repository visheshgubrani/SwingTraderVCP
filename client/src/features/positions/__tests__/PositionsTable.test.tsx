import { describe, it, expect, vi } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { PositionsTable, type PositionItem } from "../PositionsTable"

describe("PositionsTable Component", () => {
  const mockPositions: PositionItem[] = [
    {
      id: "pos-1",
      symbol: "NSE:RELIANCE-EQ",
      side: "long",
      quantity: 100,
      open_quantity: 100,
      average_entry_price: 2500.0,
      current_ltp: 2550.0,
      current_stop_loss: 2420.0,
      current_target: 2600.0,
      trailing_rule_desc: "atr: 50.00",
      realized_pnl: 0,
      unrealized_pnl: 5000.0,
      state: "open",
      opened_at: "2026-08-25T09:45:00Z",
    },
    {
      id: "pos-2",
      symbol: "NSE:INFY-EQ",
      side: "long",
      quantity: 50,
      open_quantity: 25,
      average_entry_price: 1500.0,
      current_ltp: 1600.0,
      current_stop_loss: 1500.0,
      current_target: 1700.0,
      trailing_rule_desc: "2xATR Trail",
      realized_pnl: 2500.0,
      unrealized_pnl: 2500.0,
      state: "trailing_active",
      opened_at: "2026-08-24T10:00:00Z",
    },
    {
      id: "pos-3",
      symbol: "NSE:TCS-EQ",
      side: "long",
      quantity: 40,
      open_quantity: 0,
      average_entry_price: 4000.0,
      current_ltp: 4100.0,
      current_stop_loss: 3900.0,
      current_target: 4200.0,
      trailing_rule_desc: "None",
      realized_pnl: 4000.0,
      unrealized_pnl: null,
      state: "closed",
      opened_at: "2026-08-23T11:00:00Z",
    },
  ]

  it("renders active positions count and table headers", () => {
    renderWithProviders(<PositionsTable positions={mockPositions} />)

    expect(screen.getByText("ACTIVE POSITIONS")).toBeInTheDocument()
    expect(screen.getByText("2 OPEN")).toBeInTheDocument() // pos-1 and pos-2 are non-closed
    expect(screen.getByText("STATE")).toBeInTheDocument()
    expect(screen.getByText("SYMBOL")).toBeInTheDocument()
    expect(screen.getByText("UNREALIZED P&L")).toBeInTheDocument()
  })

  it("renders position details with state badges and P&L formatting", () => {
    renderWithProviders(<PositionsTable positions={mockPositions} />)

    // Check symbols
    expect(screen.getByText("NSE:RELIANCE-EQ")).toBeInTheDocument()
    expect(screen.getByText("NSE:INFY-EQ")).toBeInTheDocument()
    expect(screen.getByText("NSE:TCS-EQ")).toBeInTheDocument()

    // Check state badges
    expect(screen.getByText("OPEN")).toBeInTheDocument()
    expect(screen.getByText("TRAILING")).toBeInTheDocument()
    expect(screen.getByText("CLOSED")).toBeInTheDocument()

    // Check quantities
    expect(screen.getByText("100 / 100")).toBeInTheDocument()
    expect(screen.getByText("25 / 50")).toBeInTheDocument()

    // Check Unrealized P&L
    expect(screen.getByText("+₹5000.00")).toBeInTheDocument()
    expect(screen.getByText("+₹2500.00")).toBeInTheDocument()
  })

  it("triggers onManualExit callback when Close Market button is clicked", async () => {
    const mockExit = vi.fn()
    const { user } = renderWithProviders(
      <PositionsTable positions={mockPositions} onManualExit={mockExit} />
    )

    const closeButtons = screen.getAllByRole("button", { name: /EXIT/i })
    expect(closeButtons.length).toBe(2) // pos-1 and pos-2

    await user.click(closeButtons[0])
    expect(mockExit).toHaveBeenCalledWith("pos-1")
  })
})
