import { describe, expect, it, vi } from "vitest"
import { fireEvent } from "@testing-library/react"
import { useLocation } from "react-router"

import type { ChartListItem } from "@/features/chart/chartList"
import { isTypingTarget, listPosition, useChartListNav } from "@/features/chart/useChartListNav"
import { renderWithProviders, screen } from "@/test/test-utils"

vi.mock("@/lib/api", () => ({ apiRequest: vi.fn().mockResolvedValue({ candles: [] }) }))

const items: ChartListItem[] = ["A", "B", "C"].map((s) => ({ fyers_symbol: `NSE:${s}-EQ`, symbol: s, name: null }))

describe("listPosition", () => {
  it("locates the symbol and its neighbours", () => {
    const middle = listPosition(items, "NSE:B-EQ")
    expect(middle.index).toBe(1)
    expect(middle.prev?.symbol).toBe("A")
    expect(middle.next?.symbol).toBe("C")
    expect(listPosition(items, "NSE:A-EQ").prev).toBeNull()
    expect(listPosition(items, "NSE:C-EQ").next).toBeNull()
  })

  it("steps into the list from the top when the symbol is not in it", () => {
    const off = listPosition(items, "NSE:ZZZ-EQ")
    expect(off.index).toBe(-1)
    expect(off.prev).toBeNull()
    expect(off.next?.symbol).toBe("A")
  })
})

describe("isTypingTarget", () => {
  it("treats fields and dialogs as typing targets", () => {
    const input = document.createElement("input")
    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    const inner = document.createElement("button")
    dialog.appendChild(inner)
    expect(isTypingTarget(input)).toBe(true)
    expect(isTypingTarget(inner)).toBe(true)
    expect(isTypingTarget(document.createElement("div"))).toBe(false)
  })
})

function Harness() {
  const location = useLocation()
  const symbol = new URLSearchParams(location.search).get("symbol")
  const nav = useChartListNav(items, symbol)
  return (
    <div>
      <span data-testid="symbol">{symbol}</span>
      <span data-testid="pos">{`${nav.index + 1}/${nav.total}`}</span>
      <input aria-label="search" />
    </div>
  )
}

describe("useChartListNav", () => {
  it("flips with arrow keys and J/K, keeping the list params", () => {
    renderWithProviders(<Harness />, { routerInitialEntries: ["/?symbol=NSE:A-EQ&list=scan&grade=A"] })
    expect(screen.getByTestId("pos")).toHaveTextContent("1/3")

    fireEvent.keyDown(window, { key: "ArrowDown" })
    expect(screen.getByTestId("symbol")).toHaveTextContent("NSE:B-EQ")
    fireEvent.keyDown(window, { key: "j" })
    expect(screen.getByTestId("symbol")).toHaveTextContent("NSE:C-EQ")
    fireEvent.keyDown(window, { key: "ArrowDown" }) // end of list
    expect(screen.getByTestId("symbol")).toHaveTextContent("NSE:C-EQ")
    fireEvent.keyDown(window, { key: "k" })
    expect(screen.getByTestId("symbol")).toHaveTextContent("NSE:B-EQ")
  })

  it("ignores keys typed into an input", () => {
    renderWithProviders(<Harness />, { routerInitialEntries: ["/?symbol=NSE:A-EQ"] })
    fireEvent.keyDown(screen.getByLabelText("search"), { key: "j" })
    expect(screen.getByTestId("symbol")).toHaveTextContent("NSE:A-EQ")
  })
})
