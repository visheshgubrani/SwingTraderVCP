import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

export interface TradeProposalItem {
  id: string
  automation_run_id: string | null
  screening_result_id: string
  instrument_id: string
  symbol: string
  as_of_date: string
  status: "pending_approval" | "approved" | "rejected" | "expired_unapproved"
  approval_deadline: string
  entry_session_date: string
  proposal_hash: string
  source_hash: string
  renderer_version: string
  prompt_version: string
  schema_version: string
  geometry_version: string
  model: string
  confidence: number
  entry_template: "single" | "two_leg" | "three_leg_front" | "three_leg_balanced"
  pivot_price: number
  initial_stop: number
  stop_distance_pct: number
  chase_ceiling: number
  t1: number
  t2: number
  t3: number
  risk_budget_pct: number
  approved_risk_budget_amount: number | null
  risk_policy_version: number
  leg_count: number
  leg_risk_allocations: number[]
  relative_volume_threshold: number
  gemini_evidence: {
    base_tightness: string
    dry_up_quality: string
    resistance_room: string
    evidence_summary: string
    contraction_anchors?: Array<{ date: string; price?: number; evidence?: string }>
  }
  geometry: {
    atr14?: number
    r_distance?: number
  }
  context_image_hash: string | null
  detail_image_hash: string | null
  live_eligible: boolean
  generated_at: string
  created_at: string
  updated_at: string
  legs?: Array<{
    id: string
    leg_index: number
    risk_allocation_pct: number
    status: string
    trigger_type: string
    trigger_price: number | null
    chase_ceiling: number | null
    relative_volume_threshold: number
    hold_required: number
    base_required: number
    hold_count: number
    base_count: number
    base_low: number | null
    base_high: number | null
    eligible_session_start: string | null
    eligible_session_end: string | null
    filled_shares: number
    filled_avg_price: number | null
  }>
}

export interface DecisionPayload {
  decision: "approved" | "rejected"
  expected_proposal_hash: string
  notes?: string
}

export interface CapacityConflict {
  id: string
  bar_timestamp: string
  status: "open" | "resolved" | "expired_skipped"
  candidates: Array<{
    leg_id: string
    symbol: string
    leg_index: number
    confidence: number
    conservative_rr: number
  }>
}

export type MarketLight = "green" | "yellow" | "red" | "unavailable"
export type SectorTier = "leading" | "neutral" | "lagging" | "unavailable"

export interface MarketContextSector {
  sector_code: string
  sector_name: string
  index_symbol: string
  ordinal_rank: number | null
  rs_rating: number | null
  raw_tier: SectorTier
  gate_tier: SectorTier
  blended_score: number | null
}

export interface MarketContextLatest {
  policy_id: string
  policy_version: string
  mode: "shadow" | "enforced"
  replay_report_hash: string | null
  reference_eod_date: string | null
  market_light: MarketLight
  exposure_multiplier: number
  trend_state: MarketLight
  breadth_state: MarketLight
  distribution_state: MarketLight
  source_hash: string | null
  evidence: Record<string, unknown>
  data_quality: Record<string, unknown>
  sectors: MarketContextSector[]
}

export interface StopStreakState {
  execution_mode: "paper" | "live"
  consecutive_count: number
  limit: number
  tripped: boolean
  tripped_at: string | null
  trip_position_id: string | null
}

export type P10RolloutStage = "shadow" | "paper" | "reduced_live" | "full_live"

export interface P10RolloutState {
  stage: P10RolloutStage
  changed_by: string
  changed_at: string
  reason: string | null
  next_stage: P10RolloutStage | null
  required_confirmation: string | null
  execution_mode: "paper" | "live"
  live_order_placement_enabled: boolean
  approvals_allowed: boolean
}

export interface PaperPortfolio {
  starting_cash: number
  cash_available: number
  invested_notional: number
  equity: number
  open_risk: number
  realized_pnl: number
  unrealized_pnl: number
  closed_trade_count: number
  win_rate: number | null
  average_r_multiple: number | null
  max_drawdown_from_start: number
  seeded_from_policy_version: number | null
  seeded_at: string
  updated_at: string
  open_positions: Array<{
    id: string
    symbol: string
    state: string
    open_quantity: number
    average_entry_price: number | null
    realized_pnl: number | null
  }>
}

const operationsKeys = {
  marketContext: ["automation", "market-context", "latest"] as const,
  stopStreak: (mode: "paper" | "live") => ["automation", "stop-streak", mode] as const,
  rollout: ["automation", "rollout"] as const,
  paperPortfolio: ["automation", "paper-portfolio"] as const,
}

async function responseJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: fallback }))
    const detail = body.detail
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : fallback,
    )
  }
  return response.json()
}

export function useMarketContext() {
  return useQuery<MarketContextLatest>({
    queryKey: operationsKeys.marketContext,
    queryFn: async () => responseJson(
      await fetch("/api/v1/automation/market-context/latest"),
      "Failed to fetch P9 market context",
    ),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
}

export function useStopStreak(mode: "paper" | "live") {
  return useQuery<StopStreakState>({
    queryKey: operationsKeys.stopStreak(mode),
    queryFn: async () => responseJson(
      await fetch(`/api/v1/automation/stop-streak/${mode}`),
      `Failed to fetch ${mode} stop streak`,
    ),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useResetStopStreak() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ mode, reason }: { mode: "paper" | "live"; reason: string }) =>
      responseJson<StopStreakState>(
        await fetch(`/api/v1/automation/stop-streak/${mode}/reset`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        }),
        "Failed to reset stop streak",
      ),
    onSuccess: (state) => {
      queryClient.setQueryData(operationsKeys.stopStreak(state.execution_mode), state)
    },
  })
}

export function useP10Rollout() {
  return useQuery<P10RolloutState>({
    queryKey: operationsKeys.rollout,
    queryFn: async () => responseJson(
      await fetch("/api/v1/automation/rollout"),
      "Failed to fetch P10 rollout stage",
    ),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function usePromoteP10Rollout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      targetStage,
      confirmation,
      changedBy,
      reason,
    }: {
      targetStage: Exclude<P10RolloutStage, "shadow">
      confirmation: string
      changedBy: string
      reason: string
    }) => responseJson<P10RolloutState>(
      await fetch("/api/v1/automation/rollout/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_stage: targetStage,
          confirmation,
          changed_by: changedBy,
          reason,
        }),
      }),
      "Failed to promote P10 rollout stage",
    ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: operationsKeys.rollout })
      void queryClient.invalidateQueries({ queryKey: operationsKeys.paperPortfolio })
    },
  })
}

export function usePaperPortfolio(enabled = true) {
  return useQuery<PaperPortfolio>({
    queryKey: operationsKeys.paperPortfolio,
    queryFn: async () => responseJson(
      await fetch("/api/v1/automation/paper-portfolio"),
      "Failed to fetch paper portfolio",
    ),
    enabled,
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: false,
  })
}

export function useResetPaperPortfolio() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      changedBy,
      reason,
    }: {
      changedBy: string
      reason: string
    }) => responseJson<{ starting_cash: number; cash_available: number }>(
      await fetch("/api/v1/automation/paper-portfolio/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirmation: "CONFIRM_PAPER_RESET",
          changed_by: changedBy,
          reason,
        }),
      }),
      "Failed to reset paper portfolio",
    ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: operationsKeys.paperPortfolio })
    },
  })
}

export function useEnforceMarketContext() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      version,
      replayReportHash,
      membershipMode,
      approvedBy,
    }: {
      version: string
      replayReportHash: string
      membershipMode: "point_in_time" | "current_membership_survivorship_biased"
      approvedBy: string
    }) => responseJson<MarketContextLatest>(
      await fetch(`/api/v1/automation/market-context/policies/${encodeURIComponent(version)}/enforce`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          replay_report_hash: replayReportHash,
          replay_membership_mode: membershipMode,
          approved_by: approvedBy,
        }),
      }),
      "Failed to enforce P9 policy",
    ),
    onSuccess: (context) => {
      queryClient.setQueryData(operationsKeys.marketContext, context)
    },
  })
}

export function useTradeProposals(statusFilter: string = "pending_approval") {
  return useQuery<TradeProposalItem[]>({
    queryKey: ["trade-proposals", statusFilter],
    queryFn: async () => {
      const res = await fetch(`/api/v1/automation/proposals?status=${encodeURIComponent(statusFilter)}`)
      if (!res.ok) throw new Error("Failed to fetch trade proposals")
      return res.json()
    },
    refetchInterval: 10000,
  })
}

export function useTradeProposal(id: string | null) {
  return useQuery<TradeProposalItem>({
    queryKey: ["trade-proposal", id],
    queryFn: async () => {
      if (!id) throw new Error("No proposal ID provided")
      const res = await fetch(`/api/v1/automation/proposals/${id}`)
      if (!res.ok) throw new Error("Failed to fetch proposal details")
      return res.json()
    },
    enabled: !!id,
  })
}

export function useRecordProposalDecision() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: DecisionPayload }) => {
      const res = await fetch(`/api/v1/automation/proposals/${id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Failed to record decision" }))
        throw new Error(err.detail || "Failed to record decision")
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["trade-proposals"] })
      queryClient.invalidateQueries({ queryKey: ["trade-proposal"] })
      queryClient.invalidateQueries({ queryKey: ["entry-supervisor-status"] })
    },
  })
}

export function useEntrySupervisorStatus() {
  return useQuery<{
    status: "active" | "inactive"
    heartbeat?: { timestamp?: string }
    armed_legs_count: number
    trigger_observed_count: number
    pending_capacity_conflicts: number
    recent_allocation_events: Array<{
      id: string
      event_type: string
      market_context_mode: "shadow" | "enforced" | null
      context_multiplier: number | null
      context_adjusted_risk_ceiling: number | null
      context_gate_reasons: string[]
      details: Record<string, unknown>
      created_at: string
    }>
  }>({
    queryKey: ["entry-supervisor-status"],
    queryFn: async () => {
      const res = await fetch("/api/v1/automation/entry-supervisor/status")
      if (!res.ok) throw new Error("Failed to fetch supervisor status")
      return res.json()
    },
    refetchInterval: 5000,
  })
}

export function useCapacityConflicts() {
  return useQuery<CapacityConflict[]>({
    queryKey: ["capacity-conflicts", "open"],
    queryFn: async () => {
      const res = await fetch("/api/v1/automation/capacity-conflicts?status=open")
      if (!res.ok) throw new Error("Failed to fetch capacity conflicts")
      return res.json()
    },
    refetchInterval: 3000,
  })
}

export function useResolveCapacityConflict() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      id,
      chosenLegId,
    }: {
      id: string
      chosenLegId: string | null
    }) => {
      const res = await fetch(`/api/v1/automation/capacity-conflicts/${id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          chosenLegId
            ? { resolution_type: "operator_selected", chosen_leg_id: chosenLegId }
            : { resolution_type: "operator_skipped" },
        ),
      })
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: "Decision failed" }))
        throw new Error(error.detail || "Decision failed")
      }
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["capacity-conflicts"] })
      queryClient.invalidateQueries({ queryKey: ["entry-supervisor-status"] })
    },
  })
}
