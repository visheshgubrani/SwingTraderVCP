import { beforeEach, describe, expect, it, vi } from "vitest"

import { renderWithProviders, screen, within } from "@/test/test-utils"
import * as AdminApiModule from "@/features/admin/api"
import * as AuthApiModule from "@/features/auth/api"
import * as HistoricalApiModule from "@/features/historical/api"
import * as ProposalsApiModule from "@/features/proposals/api"
import * as ScreenerApiModule from "@/features/screener/api"
import * as TradeApiModule from "@/features/trade/api"
import { DashboardOverview } from "../DashboardOverview"

const mutation = () => ({ mutate: vi.fn(), mutateAsync: vi.fn().mockResolvedValue({}), isPending: false, error: null }) as never

describe("Operations page", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(AuthApiModule, "useAuthStatus").mockReturnValue({ data: { healthy: true, expires_at: "2026-08-26T01:00:00Z" }, isLoading: false } as never)
    vi.spyOn(AuthApiModule, "useStartFyersLogin").mockReturnValue(mutation())
    vi.spyOn(HistoricalApiModule, "useSyncStatus").mockReturnValue({
      data: {
        state: "idle",
        total_symbols: 500,
        current_index: 500,
        candles_upserted: 5000,
        error_count: 0,
        logs: ["Sync completed for 500 symbols."],
        schedule: { enabled: true, weekdays: "Mon-Fri", time: "16:00", timezone: "Asia/Kolkata" },
        db_metrics: {
          latest_candle_date: "2026-08-25",
          expected_latest_candle_date: "2026-08-25",
          symbols_at_latest_date: 500,
          nifty500_instruments: 500,
          total_candles: 125000,
        },
      },
      isLoading: false,
    } as never)
    vi.spyOn(HistoricalApiModule, "useTriggerSync").mockReturnValue(mutation())
    vi.spyOn(HistoricalApiModule, "useCancelSync").mockReturnValue(mutation())
    vi.spyOn(ScreenerApiModule, "useScanRuns").mockReturnValue({
      data: [{ id: "scan-1", status: "succeeded", triggered_by: "eod", as_of_date: "2026-08-25", passing_count: 42, created_at: "2026-08-25T11:00:00Z", technical_config: {} }],
      isLoading: false,
    } as never)
    vi.spyOn(ProposalsApiModule, "useP10Rollout").mockReturnValue({
      data: { stage: "paper", next_stage: "reduced_live", required_confirmation: "PROMOTE_REDUCED_LIVE", approvals_allowed: true, changed_at: "2026-08-20T10:00:00Z", changed_by: "owner", reason: null },
      isLoading: false,
    } as never)
    vi.spyOn(ProposalsApiModule, "usePromoteP10Rollout").mockReturnValue(mutation())
    vi.spyOn(ProposalsApiModule, "usePaperPortfolio").mockReturnValue({ data: { cash_available: 95400, starting_cash: 500000 }, isLoading: false } as never)
    vi.spyOn(ProposalsApiModule, "useProposalBatches").mockReturnValue({ data: [], isLoading: false } as never)
    vi.spyOn(ProposalsApiModule, "useEntrySupervisorStatus").mockReturnValue({
      data: { status: "active", armed_legs_count: 1, market_data: { status: "running", ready: true } },
      isLoading: false,
    } as never)
    vi.spyOn(ProposalsApiModule, "useMarketContext").mockReturnValue({ data: undefined, isLoading: false } as never)
    vi.spyOn(ProposalsApiModule, "useStopStreak").mockReturnValue({ data: { consecutive_count: 0, limit: 3, tripped: false } } as never)
    vi.spyOn(ProposalsApiModule, "useResetStopStreak").mockReturnValue(mutation())
    vi.spyOn(ProposalsApiModule, "useEnforceMarketContext").mockReturnValue(mutation())
    vi.spyOn(TradeApiModule, "useExecutionStatus").mockReturnValue({ data: { execution_mode: "paper" }, isLoading: false } as never)
    vi.spyOn(AdminApiModule, "useKillSwitch").mockReturnValue({ data: { enabled: false, changed_at: "2026-08-25T09:00:00Z", changed_by: "owner", reason: null } } as never)
    vi.spyOn(AdminApiModule, "useSetKillSwitch").mockReturnValue(mutation())
    vi.spyOn(AdminApiModule, "useAutomationControls").mockReturnValue({
      data: [
        { control_key: "new_entries_paused", enabled: true, reason: "earnings week", changed_by: "owner_api", changed_at: "2026-08-25T09:00:00Z" },
        { control_key: "proposal_processing_paused", enabled: false, reason: null, changed_by: "owner_api", changed_at: "2026-08-25T09:00:00Z" },
      ],
    } as never)
    vi.spyOn(AdminApiModule, "useSetAutomationControl").mockReturnValue(mutation())
    vi.spyOn(AdminApiModule, "useFundamentalControls").mockReturnValue({ data: undefined } as never)
    vi.spyOn(AdminApiModule, "useSetFundamentalControl").mockReturnValue(mutation())
    vi.spyOn(AdminApiModule, "useRiskPolicy").mockReturnValue({ data: undefined, isLoading: false } as never)
    vi.spyOn(AdminApiModule, "useReconciliationRuns").mockReturnValue({ data: [], isLoading: false } as never)
    vi.spyOn(AdminApiModule, "useTriggerReconciliation").mockReturnValue(mutation())
  })

  it("shows each health signal once", () => {
    renderWithProviders(<DashboardOverview tickWorkerStatus={{ status: "running", symbol_count: 15, timestamp: "2026-08-25T12:00:00Z" }} />)
    const health = screen.getByRole("region", { name: "System health" })
    expect(within(health).getByText("Connected")).toBeInTheDocument()
    expect(within(health).getAllByText("25 Aug 2026")).toHaveLength(2) // EOD candles + last scan
    expect(within(health).getByText(/500\/500 symbols · 1,25,000 candles/)).toBeInTheDocument()
    expect(within(health).getByText(/Paper cash ₹95,400/)).toBeInTheDocument()
  })

  it("exposes the new-entry pause and requires a reason to change it", async () => {
    const setControl = vi.fn().mockResolvedValue({})
    vi.spyOn(AdminApiModule, "useSetAutomationControl").mockReturnValue({ mutateAsync: setControl, isPending: false, error: null } as never)
    const { user } = renderWithProviders(<DashboardOverview tickWorkerStatus={null} />)

    await user.click(screen.getByRole("button", { name: "Resume entries" }))
    const confirm = screen.getByRole("button", { name: "Confirm" })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText("Reason"), "earnings done")
    await user.click(confirm)
    expect(setControl).toHaveBeenCalledWith({ control: "new_entries_paused", enabled: false, reason: "earnings done" })
  })

  it("requires the typed confirmation phrase to promote the rollout", async () => {
    const promote = vi.fn()
    vi.spyOn(ProposalsApiModule, "usePromoteP10Rollout").mockReturnValue({ mutate: promote, isPending: false, error: null } as never)
    const { user } = renderWithProviders(<DashboardOverview tickWorkerStatus={null} />)

    await user.click(screen.getByRole("button", { name: /promote to reduced live/i }))
    await user.type(screen.getByLabelText("Rollout operator"), "owner")
    await user.type(screen.getByLabelText("Rollout reason"), "50 paper trades clean")
    const confirm = screen.getByRole("button", { name: "Promote" })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText("Confirmation phrase"), "PROMOTE_REDUCED_LIVE")
    await user.click(confirm)
    expect(promote).toHaveBeenCalledWith(
      { targetStage: "reduced_live", confirmation: "PROMOTE_REDUCED_LIVE", changedBy: "owner", reason: "50 paper trades clean" },
      expect.anything(),
    )
  })

  it("keeps both sync buttons", () => {
    renderWithProviders(<DashboardOverview tickWorkerStatus={null} />)
    expect(screen.getByRole("button", { name: /Sync latest EOD data/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Backfill 2 Years & Repair/i })).toBeInTheDocument()
  })
})
