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
