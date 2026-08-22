import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiRequest } from "@/lib/api"

export interface TradeProposalItem {
  id: string
  automation_run_id: string | null
  screening_result_id: string
  instrument_id: string
  symbol: string
  as_of_date: string
  status: "pending_approval" | "approved" | "rejected" | "expired_unapproved"
  entry_state?: "armed" | "trigger_observed" | "executing" | "filled" | "expired" | null
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
  entry_template: "single" | "two_leg" | "two_leg_staged" | "three_leg_front" | "three_leg_balanced"
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
    prior_uptrend?: string
    prior_uptrend_note?: string
    volume_dry_up?: string
    volume_dry_up_note?: string
    contractions?: Array<{
      index: number
      depth_pct: number | string
      high_price: number | string
      low_price: number | string
    }>
    red_flags?: string[]
    evidence_summary: string
    base_tightness?: string
    dry_up_quality?: string
    resistance_room?: string
    contraction_anchors?: Array<{ date: string; price?: number | string; anchor_type?: string; evidence?: string }>
  }
  geometry: {
    atr14?: number | string
    r_distance?: number | string
    pivot_r_distance?: string
    worst_entry_r_distance?: string
    base_chase_ceiling?: string
    rr_adjusted_chase_ceiling?: string
    r_at_pivot?: string
    r_at_base_chase_ceiling?: string
    final_r_at_chase_ceiling?: string
    t1_r?: string
    t2_r?: string
    t3_r?: string
    t2_below_2r?: boolean
    t3_below_3r?: boolean
    tick_size?: string
    final_contraction_low?: string
    anchor_merge_tolerance?: string
    pivot_grounding?: {
      is_grounded: boolean
      boundary_distance?: string | null
      tolerance?: string
      subreason?: string | null
      selected_zone?: {
        low: string
        high: string
        median: string
        most_recent_date: string
        members?: Array<{ date: string; price: string; anchor_type: string }>
      } | null
      eligible_anchors?: Array<{ date: string; price: string; anchor_type: string; eligibility: string }>
      audit_flags?: string[]
    }
    calculation_basis?: {
      pivot: {
        pivot_price: string
        grounding_status: string
        selected_zone_low?: string
        selected_zone_high?: string
        selected_zone_median?: string
        selected_zone_recent_date?: string
        boundary_distance?: string | null
        tolerance_atr: string
        tolerance_rule: string
        basis: string
      }
      stop_loss: {
        initial_stop: string
        final_contraction_low: string
        final_contraction_low_date: string
        atr14: string
        stop_buffer_multiplier: string
        stop_buffer_amount: string
        stop_distance: string
        stop_distance_pct: string
        max_allowed_stop_pct: string
        formula: string
        basis: string
      }
      entry_chase: {
        pivot_entry: string
        base_chase_ceiling?: string | null
        rr_adjusted_chase_ceiling?: string | null
        final_chase_ceiling: string
        ceiling_tightened_for_1r: boolean
        max_chase_margin: string
        max_chase_pct: string
        worst_entry_r_distance: string
        formula: string
        basis: string
      }
      targets: {
        t1: {
          price: string
          r_at_ceiling?: string | null
          r_at_pivot?: string | null
          min_required_r: string
          objective: string
        }
        t2: {
          price: string
          r_at_ceiling?: string | null
          below_2r_flag?: boolean
          objective: string
        }
        t3: {
          price: string
          r_at_ceiling?: string | null
          below_3r_flag?: boolean
          objective: string
        }
        basis: string
      }
      sizing_and_risk: {
        entry_template: string
        leg_count: number
        leg_risk_allocations: number[]
        relative_volume_threshold: number
        risk_per_trade_pct: string
        approved_risk_budget_amount?: string | null
        risk_policy_version: number
        prior_uptrend?: string
        volume_dry_up?: string
        base_tightness?: string
        dry_up_quality?: string
        resistance_room?: string
        basis: string
      }
    }
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
    derived_status?: string
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
  adjusted_pivot_price?: number
  adjusted_initial_stop?: number
  adjusted_t1?: number
  adjusted_t2?: number
  adjusted_t3?: number
  adjusted_entry_template?: "single" | "two_leg" | "two_leg_staged" | "three_leg_front" | "three_leg_balanced"
  adjusted_leg2_price?: number
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

export interface ProposalBatchStatus {
  scan_run_id: string | null
  automation_run_id: string | null
  status: "idle" | "running" | "completed" | "timed_out" | "failed"
  candidates_total: number
  candidates_processed: number
  proposals_generated: number
  proposals_rejected: number
  proposals_uncertain: number
  proposals_failed: number
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface ProposalBatchTriggerResponse {
  status: "queued" | "running" | "paused"
  scan_run_id: string
  as_of_date: string | null
  message: string
}

export interface ProposalSingleTriggerResponse {
  status: "queued"
  scan_run_id: string
  screening_result_id: string
  symbol: string
  as_of_date: string | null
  message: string
}

export type ProposalAttemptStatus =
  | "running"
  | "valid"
  | "invalid"
  | "uncertain"
  | "partial"
  | "failed"
  | "timed_out"

export interface ProposalGenerationAttempt {
  id: string
  automation_run_id: string
  screening_result_id: string
  instrument_id: string
  symbol: string
  attempt_number: number
  status: ProposalAttemptStatus
  source_hash: string
  renderer_version: string
  prompt_version: string
  schema_version: string
  geometry_version: string
  model: string
  risk_policy_version: number
  context_image_hash: string
  detail_image_hash: string
  provider_request_id: string | null
  provider_usage: Record<string, unknown>
  provider_cost: number
  structured_output: {
    verdict?: "valid" | "invalid" | "partial" | "uncertain"
    [key: string]: unknown
  } | null
  error_type: string | null
  error_message: string | null
  error_details: Record<string, unknown>
  as_of_date?: string | null
  started_at: string
  completed_at: string | null
}

export interface ProposalGenerationResults {
  automation_run_id: string
  scan_run_id: string
  status: "running" | "completed" | "timed_out" | "failed"
  candidates_total: number
  candidates_processed: number
  proposals_generated: number
  proposals_rejected: number
  proposals_uncertain: number
  proposals_failed: number
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  results: ProposalGenerationAttempt[]
}

export interface ProposalRunSummary {
  id: string
  scan_run_id: string
  status: "running" | "completed" | "timed_out" | "failed" | "idle"
  candidates_total: number
  candidates_processed: number
  proposals_generated: number
  proposals_rejected: number
  proposals_uncertain: number
  proposals_failed: number
  run_type: "batch" | "single"
  single_symbol: string | null
  as_of_date: string | null
  error_message: string | null
  started_at: string
  completed_at: string | null
  created_at: string
}

const operationsKeys = {
  marketContext: ["automation", "market-context", "latest"] as const,
  stopStreak: (mode: "paper" | "live") => ["automation", "stop-streak", mode] as const,
  rollout: ["automation", "rollout"] as const,
  paperPortfolio: ["automation", "paper-portfolio"] as const,
  proposalBatches: ["automation", "proposal-batches"] as const,
  proposalBatch: (scanRunId: string | null) =>
    ["automation", "proposal-batch", scanRunId] as const,
  proposalGenerationResults: (automationRunId: string | null) =>
    ["automation", "proposal-generation-results", automationRunId] as const,
}

export function useMarketContext() {
  return useQuery<MarketContextLatest>({
    queryKey: operationsKeys.marketContext,
    queryFn: () => apiRequest<MarketContextLatest>("/automation/market-context/latest"),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
}

export function useStopStreak(mode: "paper" | "live") {
  return useQuery<StopStreakState>({
    queryKey: operationsKeys.stopStreak(mode),
    queryFn: () => apiRequest<StopStreakState>(`/automation/stop-streak/${mode}`),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function useResetStopStreak() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ mode, reason }: { mode: "paper" | "live"; reason: string }) =>
      apiRequest<StopStreakState>(`/automation/stop-streak/${mode}/reset`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    onSuccess: (state) => {
      queryClient.setQueryData(operationsKeys.stopStreak(state.execution_mode), state)
    },
  })
}

export function useP10Rollout() {
  return useQuery<P10RolloutState>({
    queryKey: operationsKeys.rollout,
    queryFn: () => apiRequest<P10RolloutState>("/automation/rollout"),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
}

export function usePromoteP10Rollout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      targetStage,
      confirmation,
      changedBy,
      reason,
    }: {
      targetStage: Exclude<P10RolloutStage, "shadow">
      confirmation: string
      changedBy: string
      reason: string
    }) =>
      apiRequest<P10RolloutState>("/automation/rollout/promote", {
        method: "POST",
        body: JSON.stringify({
          target_stage: targetStage,
          confirmation,
          changed_by: changedBy,
          reason,
        }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: operationsKeys.rollout })
      void queryClient.invalidateQueries({ queryKey: operationsKeys.paperPortfolio })
    },
  })
}

export function usePaperPortfolio(enabled = true) {
  return useQuery<PaperPortfolio>({
    queryKey: operationsKeys.paperPortfolio,
    queryFn: () => apiRequest<PaperPortfolio>("/automation/paper-portfolio"),
    enabled,
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: false,
  })
}

export function useResetPaperPortfolio() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      changedBy,
      reason,
    }: {
      changedBy: string
      reason: string
    }) =>
      apiRequest<{ starting_cash: number; cash_available: number }>(
        "/automation/paper-portfolio/reset",
        {
          method: "POST",
          body: JSON.stringify({
            confirmation: "CONFIRM_PAPER_RESET",
            changed_by: changedBy,
            reason,
          }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: operationsKeys.paperPortfolio })
    },
  })
}

export function useEnforceMarketContext() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      version,
      replayReportHash,
      membershipMode,
      approvedBy,
    }: {
      version: string
      replayReportHash: string
      membershipMode: "point_in_time" | "current_membership_survivorship_biased"
      approvedBy: string
    }) =>
      apiRequest<MarketContextLatest>(
        `/automation/market-context/policies/${encodeURIComponent(version)}/enforce`,
        {
          method: "POST",
          body: JSON.stringify({
            replay_report_hash: replayReportHash,
            replay_membership_mode: membershipMode,
            approved_by: approvedBy,
          }),
        },
      ),
    onSuccess: (context) => {
      queryClient.setQueryData(operationsKeys.marketContext, context)
    },
  })
}

export function useProposalBatches(limit: number = 30) {
  return useQuery<ProposalRunSummary[]>({
    queryKey: [...operationsKeys.proposalBatches, limit],
    queryFn: () =>
      apiRequest<ProposalRunSummary[]>(
        `/automation/proposal-batches?limit=${limit}`,
      ),
    refetchInterval: 5000,
  })
}

export function useTradeProposals(
  statusFilter: string = "pending_approval",
  options?: {
    automationRunId?: string | null
    symbol?: string | null
    asOfDate?: string | null
    entryState?: string | null
    limit?: number
  },
) {
  const queryParams = new URLSearchParams()
  if (statusFilter) queryParams.set("status", statusFilter)
  if (options?.symbol) queryParams.set("symbol", options.symbol)
  if (options?.automationRunId) queryParams.set("automation_run_id", options.automationRunId)
  if (options?.asOfDate) queryParams.set("as_of_date", options.asOfDate)
  if (options?.entryState) queryParams.set("entry_state", options.entryState)
  if (options?.limit) queryParams.set("limit", String(options.limit))

  return useQuery<TradeProposalItem[]>({
    queryKey: [
      "trade-proposals",
      statusFilter,
      options?.symbol,
      options?.automationRunId,
      options?.asOfDate,
      options?.entryState,
      options?.limit,
    ],
    queryFn: () =>
      apiRequest<TradeProposalItem[]>(
        `/automation/proposals?${queryParams.toString()}`,
      ),
    refetchInterval: 10000,
  })
}

export function useProposalBatch(scanRunId: string | null) {
  return useQuery<ProposalBatchStatus>({
    queryKey: operationsKeys.proposalBatch(scanRunId),
    queryFn: () => {
      const query = scanRunId
        ? `?scan_run_id=${encodeURIComponent(scanRunId)}`
        : ""
      return apiRequest<ProposalBatchStatus>(
        `/automation/proposal-batches/latest${query}`,
      )
    },
    staleTime: 1_000,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 2_000 : 8_000,
  })
}

export function useProposalGenerationResults(automationRunId: string | null) {
  return useQuery<ProposalGenerationResults>({
    queryKey: operationsKeys.proposalGenerationResults(automationRunId),
    queryFn: () => {
      if (!automationRunId) throw new Error("No automation run ID provided")
      return apiRequest<ProposalGenerationResults>(
        `/automation/proposal-batches/${automationRunId}/generation-results`,
      )
    },
    enabled: !!automationRunId,
    staleTime: 1_000,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 2_000 : 8_000,
  })
}

export function useTriggerProposalBatch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (scanRunId?: string | null) =>
      apiRequest<ProposalBatchTriggerResponse>("/automation/proposal-batches", {
        method: "POST",
        body: JSON.stringify(
          scanRunId ? { scan_run_id: scanRunId } : {},
        ),
      }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({
        queryKey: operationsKeys.proposalBatch(result.scan_run_id),
      })
      void queryClient.invalidateQueries({
        queryKey: operationsKeys.proposalBatch(null),
      })
      void queryClient.invalidateQueries({ queryKey: ["trade-proposals"] })
      void queryClient.invalidateQueries({
        queryKey: ["automation", "proposal-generation-results"],
      })
    },
  })
}

export function useTriggerSingleProposal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (screeningResultId: string) =>
      apiRequest<ProposalSingleTriggerResponse>(
        `/automation/screening-results/${screeningResultId}/proposals`,
        { method: "POST" },
      ),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({
        queryKey: operationsKeys.proposalBatch(result.scan_run_id),
      })
      void queryClient.invalidateQueries({
        queryKey: operationsKeys.proposalBatch(null),
      })
      void queryClient.invalidateQueries({ queryKey: ["trade-proposals"] })
      void queryClient.invalidateQueries({
        queryKey: ["automation", "proposal-generation-results"],
      })
    },
  })
}

export function useTradeProposal(id: string | null) {
  return useQuery<TradeProposalItem>({
    queryKey: ["trade-proposal", id],
    queryFn: () => {
      if (!id) throw new Error("No proposal ID provided")
      return apiRequest<TradeProposalItem>(`/automation/proposals/${id}`)
    },
    enabled: !!id,
  })
}

export function useRecordProposalDecision() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: DecisionPayload }) =>
      apiRequest<TradeProposalItem>(`/automation/proposals/${id}/decision`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["trade-proposals"] })
      void queryClient.invalidateQueries({ queryKey: ["trade-proposal"] })
      void queryClient.invalidateQueries({ queryKey: ["entry-supervisor-status"] })
    },
  })
}

export function useEntrySupervisorStatus() {
  return useQuery<{
    status: "active" | "inactive"
    heartbeat?: { timestamp?: string }
    market_data: {
      status: string
      timestamp: string | null
      symbol_count: number
      ready: boolean
    }
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
    queryFn: () => apiRequest("/automation/entry-supervisor/status"),
    refetchInterval: 5000,
  })
}

export function useCapacityConflicts() {
  return useQuery<CapacityConflict[]>({
    queryKey: ["capacity-conflicts", "open"],
    queryFn: () => apiRequest<CapacityConflict[]>("/automation/capacity-conflicts?status=open"),
    refetchInterval: 3000,
  })
}

export function useResolveCapacityConflict() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      chosenLegId,
    }: {
      id: string
      chosenLegId: string | null
    }) =>
      apiRequest(`/automation/capacity-conflicts/${id}/decision`, {
        method: "POST",
        body: JSON.stringify(
          chosenLegId
            ? { resolution_type: "operator_selected", chosen_leg_id: chosenLegId }
            : { resolution_type: "operator_skipped" },
        ),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["capacity-conflicts"] })
      void queryClient.invalidateQueries({ queryKey: ["entry-supervisor-status"] })
    },
  })
}

export function useRejectedAttempts(filters?: {
  status?: string | null
  symbol?: string | null
  automationRunId?: string | null
  limit?: number
}) {
  const queryParams = new URLSearchParams()
  if (filters?.status && filters.status !== "all") queryParams.set("status", filters.status)
  if (filters?.symbol) queryParams.set("symbol", filters.symbol)
  if (filters?.automationRunId) queryParams.set("automation_run_id", filters.automationRunId)
  if (filters?.limit) queryParams.set("limit", String(filters.limit))

  const queryString = queryParams.toString() ? `?${queryParams.toString()}` : ""
  return useQuery<ProposalGenerationAttempt[]>({
    queryKey: [
      "automation",
      "rejected-attempts",
      filters?.status,
      filters?.symbol,
      filters?.automationRunId,
      filters?.limit,
    ],
    queryFn: () =>
      apiRequest<ProposalGenerationAttempt[]>(`/automation/rejected-attempts${queryString}`),
    refetchInterval: 5000,
  })
}

export function useProposalAttempt(id: string | null) {
  return useQuery<ProposalGenerationAttempt>({
    queryKey: ["automation", "proposal-attempt", id],
    queryFn: () => {
      if (!id) throw new Error("No attempt ID provided")
      return apiRequest<ProposalGenerationAttempt>(`/automation/attempts/${id}`)
    },
    enabled: !!id,
  })
}
