import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { useLocation } from "react-router"

import { ToastProvider } from "@/components/terminal/toast"
import { TooltipProvider } from "@/components/ui/tooltip"
import { TradingAppProvider, type TradingAppContext } from "@/features/dashboard/app-context"
import * as ScreenerApiModule from "@/features/screener/api"
import { renderWithProviders, screen, within } from "@/test/test-utils"
import * as ProposalsApiModule from "../api"
import type { TradeProposalItem } from "../api"
import { ProposalDetail } from "../ProposalDetail"
import { ProposalsHub } from "../ProposalsHub"

const appContext: TradingAppContext = {
  orderIntents: [],
  positions: [],
  rawPositions: [],
  tickWorkerStatus: null,
  chartSymbol: null,
  openChart: vi.fn(),
}

function Providers({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <TooltipProvider>
        <TradingAppProvider value={appContext}>{children}</TradingAppProvider>
      </TooltipProvider>
    </ToastProvider>
  )
}

const inTwoHours = new Date(Date.now() + 2 * 3_600_000).toISOString()

const proposal = {
  id: "prop-100",
  automation_run_id: "run-1",
  screening_result_id: "sr-1",
  instrument_id: "inst-1",
  symbol: "NSE:TCS-EQ",
  as_of_date: "2026-10-23",
  status: "pending_approval",
  entry_state: null,
  approval_deadline: inTwoHours,
  entry_session_date: "2026-10-26",
  proposal_hash: "hash_tcs_999",
  source_hash: "src_hash",
  renderer_version: "r1",
  prompt_version: "p1",
  schema_version: "s1",
  geometry_version: "g7",
  model: "google/gemini",
  confidence: 81,
  entry_template: "three_leg_front",
  pivot_price: 3500,
  initial_stop: 3300,
  stop_distance_pct: 5.71,
  chase_ceiling: 3570,
  t1: 3900,
  t2: 4100,
  t3: 4300,
  risk_budget_pct: 1,
  approved_risk_budget_amount: 5000,
  risk_policy_version: 1,
  leg_count: 3,
  leg_risk_allocations: [0.5, 0.3, 0.2],
  relative_volume_threshold: 1.5,
  entry_trigger_policy_version: "balanced_breakout_v3",
  gemini_evidence: { evidence_summary: "Three orderly contractions with volume dry-up.", confidence: 81 },
  geometry: { planned_entry: "3510.00", t1_r: "2.00" },
  context_image_hash: null,
  detail_image_hash: null,
  live_eligible: true,
  generated_at: "2026-10-23T14:00:00Z",
  created_at: "2026-10-23T14:00:00Z",
  updated_at: "2026-10-23T14:00:00Z",
  decision: null,
  decided_at: null,
  decision_notes: null,
  decision_source: null,
  position: null,
  legs: [],
  trigger_events: [],
} as unknown as TradeProposalItem

function mockDetailHooks({
  item = proposal,
  approvalsAllowed = true,
  mutate = vi.fn(),
}: { item?: TradeProposalItem; approvalsAllowed?: boolean; mutate?: ReturnType<typeof vi.fn> } = {}) {
  vi.spyOn(ProposalsApiModule, "useTradeProposal").mockReturnValue({ data: item, isLoading: false } as never)
  vi.spyOn(ProposalsApiModule, "useP10Rollout").mockReturnValue({
    data: { stage: approvalsAllowed ? "paper" : "shadow", approvals_allowed: approvalsAllowed },
  } as never)
  vi.spyOn(ProposalsApiModule, "useEntrySupervisorStatus").mockReturnValue({ data: undefined } as never)
  vi.spyOn(ProposalsApiModule, "useRecordProposalDecision").mockReturnValue({ mutate, isPending: false, error: null } as never)
  return mutate
}

describe("ProposalDetail", () => {
  beforeEach(() => vi.restoreAllMocks())

  it("approves with only the decision, the proposal hash and notes", async () => {
    const mutate = mockDetailHooks()
    const { user } = renderWithProviders(
      <Providers>
        <ProposalDetail proposalId="prop-100" />
      </Providers>,
    )

    await user.type(screen.getByLabelText("Decision notes"), "clean base")
    await user.click(screen.getByRole("button", { name: /approve & arm leg 1/i }))

    expect(mutate).toHaveBeenCalledWith(
      { id: "prop-100", payload: { decision: "approved", expected_proposal_hash: "hash_tcs_999", notes: "clean base" } },
      expect.anything(),
    )
  })

  it("rejects without notes", async () => {
    const mutate = mockDetailHooks()
    const { user } = renderWithProviders(
      <Providers>
        <ProposalDetail proposalId="prop-100" />
      </Providers>,
    )
    await user.click(screen.getByRole("button", { name: /^reject$/i }))
    expect(mutate).toHaveBeenCalledWith(
      { id: "prop-100", payload: { decision: "rejected", expected_proposal_hash: "hash_tcs_999", notes: undefined } },
      expect.anything(),
    )
  })

  it("never shows a browser-computed share quantity", () => {
    mockDetailHooks()
    renderWithProviders(
      <Providers>
        <ProposalDetail proposalId="prop-100" />
      </Providers>,
    )
    expect(screen.queryByText(/shares/i)).not.toBeInTheDocument()
    expect(screen.getByText("Three orderly contractions with volume dry-up.")).toBeInTheDocument()
  })

  it("blocks both decisions on a review-only proposal", () => {
    mockDetailHooks({ item: { ...proposal, live_eligible: false } })
    renderWithProviders(
      <Providers>
        <ProposalDetail proposalId="prop-100" />
      </Providers>,
    )
    expect(screen.getByRole("button", { name: /approve & arm leg 1/i })).toBeDisabled()
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeDisabled()
    expect(screen.getByText(/review-only proposal/i)).toBeInTheDocument()
  })

  it("allows reject but not approve when the rollout stage blocks approvals", () => {
    mockDetailHooks({ approvalsAllowed: false })
    renderWithProviders(
      <Providers>
        <ProposalDetail proposalId="prop-100" />
      </Providers>,
    )
    expect(screen.getByRole("button", { name: /approve & arm leg 1/i })).toBeDisabled()
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeEnabled()
  })

  it("labels a paper auto-approval as such", () => {
    mockDetailHooks({
      item: { ...proposal, status: "approved", decision: "approved", decision_source: "paper_auto", decided_at: "2026-10-24T03:00:00Z" },
    })
    renderWithProviders(
      <Providers>
        <ProposalDetail proposalId="prop-100" />
      </Providers>,
    )
    expect(screen.getByText(/auto-approved \(paper testing\)/i)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument()
  })
})

function LocationProbe() {
  const location = useLocation()
  return <span data-testid="search">{location.search}</span>
}

describe("ProposalsHub", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(ScreenerApiModule, "useScanRuns").mockReturnValue({ data: [] } as never)
    vi.spyOn(ProposalsApiModule, "useTradeProposals").mockImplementation(
      (status) => ({ data: status === "pending_approval" ? [proposal] : [], isLoading: false, error: null }) as never,
    )
    vi.spyOn(ProposalsApiModule, "useFormingPatterns").mockReturnValue({ data: [], isLoading: false } as never)
    vi.spyOn(ProposalsApiModule, "useEntrySupervisorStatus").mockReturnValue({ data: { status: "active", market_data: { status: "running", ready: true } } } as never)
    vi.spyOn(ProposalsApiModule, "useCapacityConflicts").mockReturnValue({ data: [] } as never)
    vi.spyOn(ProposalsApiModule, "useResolveCapacityConflict").mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
    vi.spyOn(ProposalsApiModule, "useProposalBatch").mockReturnValue({ data: undefined } as never)
    vi.spyOn(ProposalsApiModule, "useTriggerProposalBatch").mockReturnValue({ mutate: vi.fn(), isPending: false, error: null } as never)
    vi.spyOn(ProposalsApiModule, "useMarketContext").mockReturnValue({ data: undefined } as never)
    mockDetailHooks()
  })

  it("lists pending proposals with tab counts and opens the detail drawer in the URL", async () => {
    const { user } = renderWithProviders(
      <Providers>
        <ProposalsHub />
        <LocationProbe />
      </Providers>,
      { routerInitialEntries: ["/proposals"] },
    )

    expect(screen.getByRole("tab", { name: /^pending\s*1$/i })).toHaveAttribute("aria-selected", "true")
    for (const label of ["In play", "History", "Not proposed", "Forming", "Runs", "Paper ledger"]) {
      expect(screen.getByRole("tab", { name: new RegExp(label, "i") })).toBeInTheDocument()
    }

    await user.click(screen.getByText("TCS"))
    expect(screen.getByTestId("search")).toHaveTextContent("proposal=prop-100")
    expect(await screen.findByRole("button", { name: /approve & arm leg 1/i })).toBeInTheDocument()
  })

  it("switches tabs through the URL", async () => {
    vi.spyOn(ProposalsApiModule, "useProposalHistory").mockReturnValue({
      data: { pages: [[{ ...proposal, status: "rejected", decision: "rejected", decided_at: "2026-10-24T03:00:00Z", decision_source: "operator" }]] },
      isLoading: false,
      error: null,
      hasNextPage: false,
    } as never)
    const { user } = renderWithProviders(
      <Providers>
        <ProposalsHub />
        <LocationProbe />
      </Providers>,
      { routerInitialEntries: ["/proposals"] },
    )
    await user.click(screen.getByRole("tab", { name: /history/i }))
    expect(screen.getByTestId("search")).toHaveTextContent("tab=history")
    expect(within(screen.getByRole("table")).getByText("Rejected")).toBeInTheDocument()
  })
})
