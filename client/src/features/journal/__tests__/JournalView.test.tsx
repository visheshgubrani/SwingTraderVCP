import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { JournalView } from "../JournalView"
import * as JournalApiModule from "../api"

describe("JournalView Component", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(JournalApiModule, "useJournalSummary").mockReturnValue({
      data: {
        summary: {
          trade_count: 12,
          win_rate: 67.0,
          net_pnl: 24500.5,
          profit_factor: 2.8,
          avg_r: 1.5,
        },
      },
      isLoading: false,
    } as any)
    vi.spyOn(JournalApiModule, "useUpdateJournalReview").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
    vi.spyOn(JournalApiModule, "useCreateAiCoachRun").mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: "ai-1" }),
      isPending: false,
    } as any)
    vi.spyOn(JournalApiModule, "useAiCoachRun").mockReturnValue({
      data: null,
      isLoading: false,
    } as any)
  })

  it("renders journal header and summary metrics", () => {
    vi.spyOn(JournalApiModule, "useJournalEntries").mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
    } as any)
    vi.spyOn(JournalApiModule, "useJournalEntry").mockReturnValue({
      data: null,
      isLoading: false,
    } as any)

    renderWithProviders(<JournalView />)

    expect(screen.getByText("TRADE JOURNAL")).toBeInTheDocument()
    expect(screen.getByText(/Scan with AI/i)).toBeInTheDocument()
  })

  it("renders journal entries list with regime tags and P&L", () => {
    const mockEntries = [
      {
        id: "j-1",
        symbol: "NSE:TITAN-EQ",
        side: "long",
        status: "closed",
        entry_price: 3000.0,
        exit_price: 3300.0,
        entry_time: "2026-08-20T09:35:00Z",
        exit_time: "2026-08-22T14:30:00Z",
        net_pnl: 15000.0,
        net_r_multiple: 3.0,
        regime: "confirmed_uptrend",
        execution_rating: 5,
      },
      {
        id: "j-2",
        symbol: "NSE:SBIN-EQ",
        side: "long",
        status: "closed",
        entry_price: 800.0,
        exit_price: 780.0,
        entry_time: "2026-08-21T10:00:00Z",
        exit_time: "2026-08-21T15:00:00Z",
        net_pnl: -2000.0,
        net_r_multiple: -1.0,
        regime: "rally_attempt",
        execution_rating: 3,
      },
    ]

    vi.spyOn(JournalApiModule, "useJournalEntries").mockReturnValue({
      data: { items: mockEntries, total: 2 },
      isLoading: false,
    } as any)
    vi.spyOn(JournalApiModule, "useJournalEntry").mockReturnValue({
      data: mockEntries[0],
      isLoading: false,
    } as any)

    renderWithProviders(<JournalView />)

    expect(screen.getAllByText("NSE:TITAN-EQ").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("NSE:SBIN-EQ")).toBeInTheDocument()
    expect(screen.getAllByText("+₹15000.00").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("₹-2000.00")).toBeInTheDocument()
    expect(screen.getByText(/\+3\.00R/i)).toBeInTheDocument()
    expect(screen.getAllByText(/confirmed_uptrend/i).length).toBeGreaterThanOrEqual(1)
  })
})
