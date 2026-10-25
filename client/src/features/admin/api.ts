import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

export interface KillSwitchState {
  control_key: "global_kill_switch"
  enabled: boolean
  reason: string | null
  changed_by: string
  changed_at: string
  redis_published: boolean | null
}

export interface FundamentalControlState {
  control_key: string
  enabled: boolean
  paused: boolean
  reason: string | null
  changed_by: string
  changed_at: string
  redis_published: boolean | null
}

export interface FundamentalControlsState {
  processing: FundamentalControlState
  ai: FundamentalControlState
}

export const systemControlKeys = {
  all: ["system-controls"] as const,
  killSwitch: () => [...systemControlKeys.all, "global-kill-switch"] as const,
  fundamentals: () => [...systemControlKeys.all, "fundamentals"] as const,
}

export function useFundamentalControls() {
  return useQuery({
    queryKey: systemControlKeys.fundamentals(),
    queryFn: () => apiRequest<FundamentalControlsState>("/system/fundamentals-controls"),
    staleTime: 2_000,
    refetchInterval: 5_000,
    retry: 1,
  })
}

export function useSetFundamentalControl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ control, paused, reason }: { control: "processing" | "ai"; paused: boolean; reason: string }) =>
      apiRequest<FundamentalControlState>(`/system/fundamentals-controls/${control}`, {
        method: "PUT",
        body: JSON.stringify({ paused, reason }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: systemControlKeys.fundamentals() })
    },
  })
}

export function useKillSwitch() {
  return useQuery({
    queryKey: systemControlKeys.killSwitch(),
    queryFn: () => apiRequest<KillSwitchState>("/system/kill-switch"),
    staleTime: 2_000,
    refetchInterval: 5_000,
  })
}

export function useSetKillSwitch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      enabled,
      reason,
    }: {
      enabled: boolean
      reason: string
    }) =>
      apiRequest<KillSwitchState>("/system/kill-switch", {
        method: "PUT",
        body: JSON.stringify({ enabled, reason }),
      }),
    onSuccess: (state) => {
      queryClient.setQueryData(systemControlKeys.killSwitch(), state)
    },
  })
}

/* ── P10 automation pauses (system_controls via /automation/controls) ── */

export type AutomationControlKey = "global_kill_switch" | "proposal_processing_paused" | "new_entries_paused"

export interface AutomationControlState {
  control_key: AutomationControlKey
  enabled: boolean
  reason: string | null
  changed_by: string
  changed_at: string
}

export const automationControlKeys = {
  controls: ["automation", "controls"] as const,
  riskPolicy: ["automation", "risk-policy"] as const,
  reconciliationRuns: ["system", "reconciliation", "runs"] as const,
}

export function useAutomationControls() {
  return useQuery({
    queryKey: automationControlKeys.controls,
    queryFn: () => apiRequest<AutomationControlState[]>("/automation/controls"),
    staleTime: 2_000,
    refetchInterval: 5_000,
  })
}

export function useSetAutomationControl() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      control,
      enabled,
      reason,
    }: {
      control: Exclude<AutomationControlKey, "global_kill_switch">
      enabled: boolean
      reason: string
    }) =>
      apiRequest<AutomationControlState>(`/automation/controls/${control}`, {
        method: "PUT",
        body: JSON.stringify({ enabled, reason }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: automationControlKeys.controls })
    },
  })
}

/* ── active risk policy (read-only in the UI) ── */

export interface RiskPolicy {
  id: string
  version: number
  name: string
  is_active: boolean
  risk_per_trade_pct: number | string
  max_total_open_risk_pct: number | string
  max_single_name_notional_pct: number | string
  max_sector_notional_pct: number | string
  max_cluster_notional_pct: number | string
  correlation_cluster_threshold: number | string
  correlation_lookback_sessions: number
  daily_loss_limit_pct: number | string
  max_open_positions: number
  deployable_capital_override: number | string | null
  consecutive_stop_limit: number
  created_at: string
  updated_at: string
}

export function useRiskPolicy() {
  return useQuery({
    queryKey: automationControlKeys.riskPolicy,
    queryFn: () => apiRequest<RiskPolicy>("/automation/risk-policy"),
    staleTime: 60_000,
    retry: false,
  })
}

/* ── reconciliation ── */

export interface ReconciliationRun {
  id: string
  status: "running" | "succeeded" | "failed"
  started_at: string
  completed_at: string | null
  discrepancies_found: number
  summary: Record<string, unknown>
  error_message: string | null
}

export function useReconciliationRuns(limit = 10) {
  return useQuery({
    queryKey: [...automationControlKeys.reconciliationRuns, limit],
    queryFn: () => apiRequest<ReconciliationRun[]>(`/system/reconciliation/runs?limit=${limit}`),
    refetchInterval: (query) => (query.state.data?.some((run) => run.status === "running") ? 3_000 : 30_000),
  })
}

export function useTriggerReconciliation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiRequest<{ status: string; job_id: string | null; message: string }>("/system/reconciliation/run", {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: automationControlKeys.reconciliationRuns })
    },
  })
}
