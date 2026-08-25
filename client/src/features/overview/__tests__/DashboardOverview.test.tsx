import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { DashboardOverview } from "../DashboardOverview"
import * as AuthApiModule from "@/features/auth/api"
import * as HistoricalApiModule from "@/features/historical/api"
import * as ScreenerApiModule from "@/features/screener/api"
import * as ProposalsApiModule from "@/features/proposals/api"

describe("DashboardOverview Operations Component", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(AuthApiModule, "useAuthStatus").mockReturnValue({
      data: { healthy: true, expires_at: "2026-08-26T06:00:00Z" },
      isLoading: false,
    } as any)
    vi.spyOn(AuthApiModule, "useStartFyersLogin").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
    vi.spyOn(HistoricalApiModule, "useSyncStatus").mockReturnValue({
      data: {
        status: "idle",
        state: "idle",
        total_symbols: 500,
        current_index: 500,
        candles_upserted: 5000,
        logs: ["Sync completed for 500 symbols."],
        last_completed_at: "2026-08-25T16:00:00Z",
        schedule: {
          enabled: true,
          weekdays: "Mon-Fri",
          time: "16:00",
          timezone: "Asia/Kolkata",
        },
        db_metrics: {
          latest_candle_date: "2026-08-25",
          symbols_at_latest_date: 500,
          nifty500_instruments: 500,
          total_candles: 125000,
        },
      },
      isLoading: false,
    } as any)
    vi.spyOn(HistoricalApiModule, "useTriggerSync").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
    vi.spyOn(HistoricalApiModule, "useCancelSync").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
    vi.spyOn(ScreenerApiModule, "useScanRuns").mockReturnValue({
      data: [{
        id: "scan-1",
        status: "completed",
        run_type: "production",
        universe_code: "NIFTY_500",
        technical_config: { pipeline_version: "v4" },
        created_at: "2026-08-25T16:30:00Z"
      }],
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useP10Rollout").mockReturnValue({
      data: { stage: "paper", approvals_allowed: true },
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "usePromoteP10Rollout").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "usePaperPortfolio").mockReturnValue({
      data: { cash_available: 95400.0, starting_cash: 100000.0 },
      isLoading: false,
    } as any)
  })

  it("renders workstation operational status cards", () => {
    renderWithProviders(
      <DashboardOverview
        tickWorkerStatus={{
          status: "running",
          symbol_count: 15,
          timestamp: "2026-08-25T12:00:00Z",
        }}
      />
    )

    // Fyers Auth
    expect(screen.getByText("FYERS AUTH")).toBeInTheDocument()
    expect(screen.getByText("Connected")).toBeInTheDocument()

    // Latest Stored EOD
    expect(screen.getByText("LATEST STORED EOD")).toBeInTheDocument()
    expect(screen.getByText("2026-08-25")).toBeInTheDocument()

    // Candle Database
    expect(screen.getByText("CANDLE DATABASE")).toBeInTheDocument()
    expect(screen.getByText("1,25,000")).toBeInTheDocument()

    // Rollout Stage
    expect(screen.getByText("P10 ROLLOUT")).toBeInTheDocument()
    expect(screen.getByText("paper")).toBeInTheDocument()
  })

  it("renders Paper portfolio cash balance when in paper stage", () => {
    renderWithProviders(
      <DashboardOverview
        tickWorkerStatus={{
          status: "running",
          symbol_count: 5,
          timestamp: "2026-08-25T12:00:00Z",
        }}
      />
    )

    expect(screen.getByText("P10 ROLLOUT")).toBeInTheDocument()
    expect(screen.getByText(/Cash ₹95,400/i)).toBeInTheDocument()
  })
})
