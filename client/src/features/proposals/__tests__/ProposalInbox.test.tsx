import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderWithProviders, screen } from "@/test/test-utils"
import { ProposalInbox } from "../ProposalInbox"
import { ProposalDetailModal } from "../ProposalDetailModal"
import * as ProposalsApiModule from "../api"
import * as ScreenerApiModule from "@/features/screener/api"

describe("ProposalInbox Component", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(ScreenerApiModule, "useScanRuns").mockReturnValue({
      data: [
        { id: "scan-1", status: "completed", run_type: "production", created_at: "2026-08-25T08:00:00Z" },
      ],
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useProposalBatches").mockReturnValue({
      data: [{ id: "batch-1", created_at: "2026-08-25T08:30:00Z", status: "completed" }],
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useEntrySupervisorStatus").mockReturnValue({
      data: { status: "running", active_legs_count: 2 },
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useCapacityConflicts").mockReturnValue({
      data: [],
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useProposalBatch").mockReturnValue({
      data: { automation_run_id: "batch-1" },
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useTriggerProposalBatch").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useRejectedAttempts").mockReturnValue({
      data: [],
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useFormingPatterns").mockReturnValue({
      data: [],
      isLoading: false,
    } as any)
  })

  it("renders status filter tabs and search bar", () => {
    vi.spyOn(ProposalsApiModule, "useTradeProposals").mockReturnValue({
      data: [],
      isLoading: false,
    } as any)

    renderWithProviders(<ProposalInbox />)

    expect(screen.getByText("Pending Approval")).toBeInTheDocument()
    expect(screen.getByText("Approved")).toBeInTheDocument()
    expect(screen.getByText("Rejected by Operator")).toBeInTheDocument()
    expect(screen.getByText("Expired")).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Search symbol/i)).toBeInTheDocument()
  })

  it("renders pending proposals list with badges and action links", () => {
    const mockProposals = [
      {
        id: "prop-1",
        symbol: "NSE:RELIANCE-EQ",
        status: "pending_approval",
        entry_template: "two_leg",
        pivot_price: 2500,
        planned_entry: 2505,
        initial_stop: 2420,
        t1: 2600,
        t2: 2700,
        t3: 2800,
        chase_ceiling: 2530,
        scanner_score: 91.5,
        gemini_confidence: 88,
        proposal_hash: "hash123",
        geometry: {
          planned_entry: 2505,
          initial_stop: 2420,
          t1: 2600,
          t2: 2700,
          t3: 2800,
        },
      },
    ]

    vi.spyOn(ProposalsApiModule, "useTradeProposals").mockReturnValue({
      data: mockProposals,
      isLoading: false,
    } as any)

    renderWithProviders(<ProposalInbox />)

    expect(screen.getByText("NSE:RELIANCE-EQ")).toBeInTheDocument()
    expect(screen.getByText("TWO_LEG")).toBeInTheDocument()
    expect(screen.getByText("₹2500.00")).toBeInTheDocument()
    expect(screen.getByText("₹2600.00")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Review Plan/i })).toBeInTheDocument()
  })
})

describe("ProposalDetailModal Approval Action Flow", () => {
  const sampleProposal = {
    id: "prop-100",
    symbol: "NSE:TCS-EQ",
    status: "pending_approval",
    entry_template: "three_leg_front",
    pivot_price: 4000,
    planned_entry: 4010,
    initial_stop: 3880,
    t1: 4200,
    t2: 4400,
    t3: 4600,
    chase_ceiling: 4050,
    proposal_hash: "hash_tcs_999",
    approved_risk_budget_amount: 1000,
    geometry: {
      planned_entry: 4010,
      initial_stop: 3880,
      t1_r: 2.0,
      t2_r: 3.5,
      t3_r: 5.0,
    },
    gemini_evidence: {
      confidence: 90,
      red_flags: [],
      evidence_summary: "Strong cup and handle contraction with volume dry up.",
    },
  } as any

  it("renders proposal details and level formulas correctly", () => {
    vi.spyOn(ProposalsApiModule, "useTradeProposal").mockReturnValue({
      data: sampleProposal,
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useP10Rollout").mockReturnValue({
      data: { stage: "paper", approvals_allowed: true },
      isLoading: false,
    } as any)

    renderWithProviders(
      <ProposalDetailModal
        proposal={sampleProposal}
        open={true}
        onOpenChange={vi.fn()}
      />
    )

    expect(screen.getByText("NSE:TCS-EQ")).toBeInTheDocument()
    expect(screen.getByText("THREE_LEG_FRONT")).toBeInTheDocument()
    expect(screen.getByText(/pending approval/i)).toBeInTheDocument()
    expect(screen.getByText("Strong cup and handle contraction with volume dry up.")).toBeInTheDocument()
  })

  it("allows approving a pending proposal when approvals_allowed is true", async () => {
    const mockRecordDecision = vi.fn().mockResolvedValue({})
    vi.spyOn(ProposalsApiModule, "useRecordProposalDecision").mockReturnValue({
      mutateAsync: mockRecordDecision,
      isPending: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useTradeProposal").mockReturnValue({
      data: sampleProposal,
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useP10Rollout").mockReturnValue({
      data: { stage: "paper", approvals_allowed: true },
      isLoading: false,
    } as any)

    const onOpenChange = vi.fn()
    const { user } = renderWithProviders(
      <ProposalDetailModal
        proposal={sampleProposal}
        open={true}
        onOpenChange={onOpenChange}
      />
    )

    const approveBtn = screen.getByRole("button", { name: /Approve & Arm Leg 1/i })
    expect(approveBtn).toBeEnabled()

    await user.click(approveBtn)

    expect(mockRecordDecision).toHaveBeenCalledWith({
      id: "prop-100",
      payload: {
        decision: "approved",
        expected_proposal_hash: "hash_tcs_999",
        notes: undefined,
      },
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("allows rejecting a pending proposal", async () => {
    const mockRecordDecision = vi.fn().mockResolvedValue({})
    vi.spyOn(ProposalsApiModule, "useRecordProposalDecision").mockReturnValue({
      mutateAsync: mockRecordDecision,
      isPending: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useTradeProposal").mockReturnValue({
      data: sampleProposal,
      isLoading: false,
    } as any)
    vi.spyOn(ProposalsApiModule, "useP10Rollout").mockReturnValue({
      data: { stage: "paper", approvals_allowed: true },
      isLoading: false,
    } as any)

    const onOpenChange = vi.fn()
    const { user } = renderWithProviders(
      <ProposalDetailModal
        proposal={sampleProposal}
        open={true}
        onOpenChange={onOpenChange}
      />
    )

    const rejectBtn = screen.getByRole("button", { name: /Reject/i })
    await user.click(rejectBtn)

    expect(mockRecordDecision).toHaveBeenCalledWith({
      id: "prop-100",
      payload: {
        decision: "rejected",
        expected_proposal_hash: "hash_tcs_999",
        notes: undefined,
      },
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
