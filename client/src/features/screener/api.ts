import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

export type ScanRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"

export interface ScanRun {
  id: string
  universe_code: string
  status: ScanRunStatus
  triggered_by: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  technical_config: {
    pipeline_version?: string
    [key: string]: unknown
  }
  created_at: string
  passing_count: number
}

export interface ScanResult {
  id: string
  rank: number
  symbol: string
  name: string | null
  fyers_symbol: string
  close_price: number
  sma_50: number
  sma_150: number
  sma_200: number
  avg_volume_20: number
  pct_from_52w_high: number
  rs_rating: number
  adtv_crore: number
  atr_ratio: number
  atr_ratio_3m_low: number
  bb_width: number
  bb_width_20th_pct: number
  volume_dry_up_ratio: number
  criteria_matches: Record<string, boolean>
  llm_status:
    | "not_requested"
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "skipped"
  llm_verdict: "pass" | "fail" | "uncertain" | null
  llm_flags: {
    schema_version?: string
    summary?: string
    criteria?: Array<{
      name: string
      status: "positive" | "negative" | "mixed" | "unknown" | "not_applicable"
      explanation: string
      evidence_keys: string[]
    }>
    red_flags?: string[]
    missing_data?: string[]
    error?: {
      type?: string
      message?: string
    }
  }
  llm_checked_at: string | null
  fundamental_snapshot_id: string | null
  fundamentals_provenance: {
    provider: string
    statement_type: "consolidated" | "standalone"
    fetched_at: string
    latest_annual_period: string | null
    latest_quarterly_period: string | null
  } | null
  reviewer_status: "pending" | "watchlisted" | "rejected" | "trade_planned"
}

interface ScanTriggerResponse {
  status: "queued"
  scan_run_id: string
  message: string
}

export const screeningKeys = {
  all: ["screening"] as const,
  runs: () => [...screeningKeys.all, "runs"] as const,
  results: () => [...screeningKeys.all, "results"] as const,
  runResults: (runId: string | null) =>
    [...screeningKeys.results(), { runId }] as const,
}

function hasActiveRun(runs?: ScanRun[]) {
  return runs?.some(
    (run) => run.status === "queued" || run.status === "running",
  )
}

function hasActiveAnnotations(results?: ScanResult[]) {
  return results?.some(
    (result) =>
      result.llm_status === "queued" || result.llm_status === "running",
  )
}

export function useScanRuns() {
  return useQuery({
    queryKey: screeningKeys.runs(),
    queryFn: () => apiRequest<ScanRun[]>("/screening/runs"),
    staleTime: 1_000,
    refetchInterval: (query) =>
      hasActiveRun(query.state.data) ? 1_500 : 5_000,
    retry: false,
  })
}

export function useScanResults(
  runId: string | null,
  status?: ScanRunStatus,
) {
  return useQuery({
    queryKey: screeningKeys.runResults(runId),
    queryFn: () =>
      apiRequest<ScanResult[]>(`/screening/runs/${runId}/results`),
    enabled: Boolean(runId) && status === "succeeded",
    staleTime: (query) =>
      hasActiveAnnotations(query.state.data)
        ? 1_000
        : Number.POSITIVE_INFINITY,
    refetchInterval: (query) =>
      hasActiveAnnotations(query.state.data) ? 1_500 : false,
    retry: 1,
  })
}

export function useTriggerScan() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiRequest<ScanTriggerResponse>("/screening/scan", {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: screeningKeys.runs(),
      })
    },
  })
}
