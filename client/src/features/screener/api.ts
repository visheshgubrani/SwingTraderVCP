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

export type TechnicalScoreGrade = "A" | "B" | "C" | "D"
export type FundamentalFitGrade = TechnicalScoreGrade | "insufficient"

export interface FundamentalMetric {
  key: string
  label: string
  value: number | null
  unit: string | null
  weight: number
  points: number
  available: boolean
  status: "positive" | "negative" | "mixed" | "unknown" | "not_applicable"
  evidence_keys: string[]
  unavailable_reason: string | null
}

export interface FundamentalComponent {
  name: string
  earned_points: number
  available_points: number
  max_points: number
  metrics: FundamentalMetric[]
}

export interface FundamentalAssessment {
  rubric_version: string
  score: number | null
  grade: FundamentalFitGrade
  coverage_pct: number
  earned_points: number
  available_points: number
  max_points: number
  components: FundamentalComponent[]
  red_flags: string[]
  provider_limitations: string[]
  insufficient_reason: string | null
}

export interface TechnicalScoreComponent {
  points: number
  max_points: number
  raw_value: unknown
}

export interface ScanResult {
  id: string
  rank: number
  symbol: string
  name: string | null
  fyers_symbol: string
  technical_score: number | null
  score_grade: TechnicalScoreGrade | null
  score_components: Record<string, TechnicalScoreComponent>
  eligibility: Record<string, boolean>
  core_checks: Record<string, boolean>
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
  atr_proximity_factor: number | null
  bb_width: number
  bb_width_20th_pct: number
  bb_width_percentile: number | null
  volume_dry_up_ratio: number
  up_down_volume_ratio: number | null
  pocket_pivot_age: number | null
  rs_line: number | null
  rs_line_high_52w: number | null
  rs_line_pct_off_high: number | null
  rs_benchmark_symbol: string | null
  rs_benchmark_source: string | null
  criteria_matches: Record<string, boolean>
  fundamental_selected: boolean
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
    ai_skip_reason?: string | null
  }
  llm_checked_at: string | null
  fundamental_status: string
  fundamental_verdict: "pass" | "fail" | "uncertain" | null
  fundamental_scorecard: Record<string, unknown>
  fundamental_assessment: FundamentalAssessment | null
  ai_status: string
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

export interface FundamentalHistoryPoint {
  period: string
  value?: number
  value_pct?: number
  provider_change_pct?: number | null
}

export interface FundamentalEvidence {
  label: string
  value: unknown
  unit?: string | null
  periods?: string[]
}

export interface FundamentalNormalizedFacts {
  schema_version?: string
  company?: {
    isin?: string
    symbol?: string
    name?: string | null
    sector?: string | null
    industry?: string | null
    description?: string | null
    is_financial_sector?: boolean
  }
  statement_type?: string
  periods?: {
    latest_annual?: string | null
    latest_quarterly?: string | null
  }
  histories?: {
    annual?: Record<string, FundamentalHistoryPoint[] | null>
    quarterly?: Record<string, FundamentalHistoryPoint[] | null>
    shareholding?: Record<string, FundamentalHistoryPoint[]>
  }
  ratios?: Record<
    string,
    { company?: number | null; sector?: number | null }
  >
  applicability?: Record<string, string>
  coverage?: Record<string, string>
  provider_limitations?: string[]
  provider_sections?: Record<string, unknown>
  evidence?: Record<string, FundamentalEvidence>
  missing_data?: string[]
}

export interface FundamentalDetail {
  result_id: string
  scan_run_id: string
  instrument: {
    symbol: string
    name: string | null
    fyers_symbol: string
  }
  fundamental: {
    status: string
    assessment: FundamentalAssessment | null
    scorecard: Record<string, unknown>
    missing_data: string[]
    provider_limitations: string[]
    error: { type?: string | null; message?: string | null } | null
  }
  ai_opinion: {
    status: string
    verdict: ScanResult["llm_verdict"]
    checked_at: string | null
    summary: string | null
    verdict_reference_ids: string[]
    error: { type?: string | null; message?: string | null } | null
    model: Record<string, unknown> | null
    strengths: Array<{ text: string; reference_ids: string[] }>
    risks: Array<{ text: string; reference_ids: string[] }>
    review_focus: Array<{ text: string; reference_ids: string[] }>
    skip_reason: string | null
  }
  snapshot: {
    id: string
    provider: string
    statement_type: "consolidated" | "standalone"
    fetched_at: string
    latest_annual_period: string | null
    latest_quarterly_period: string | null
    normalized_facts: FundamentalNormalizedFacts
  } | null
}

export interface FundamentalTrace {
  result_id: string
  source: {
    snapshot_id: string | null
    provider: string | null
    statement_type: string | null
    fetched_at: string | null
    content_hash: string | null
    endpoint_manifest: Array<Record<string, unknown>>
    raw_payload: Record<string, unknown> | null
    contract_valid: boolean | null
    contract_error: string | null
  }
  normalized: {
    schema_version: string | null
    facts: FundamentalNormalizedFacts
  }
  python_fit: {
    rubric_version: string | null
    scorecard: Record<string, unknown>
    contract_valid: boolean
    unresolved_reference_ids: string[]
  }
  ai_request: Record<string, unknown> | null
  ai_attempts: Array<{
    id: string
    attempt_number: number
    status: string
    model: string
    reasoning_effort: string
    prompt_version: string
    response_schema: string
    input_hash: string
    request_payload: Record<string, unknown>
    response_payload: Record<string, unknown> | null
    http_status: number | null
    request_id: string | null
    usage: Record<string, unknown>
    cost: number
    error_code: string | null
    error_message: string | null
    started_at: string
    completed_at: string | null
  }>
  legacy_response_captured: boolean
  pipeline_errors: Record<string, unknown>
}

interface ScanTriggerResponse {
  status: "queued"
  scan_run_id: string
  message: string
}

export interface FundamentalPassProgress {
  analysis_run_id: string
  scan_run_id: string
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled"
  current_rank: number | null
  current_symbol: string | null
  counts: Record<string, number>
  provider_requests: number
  token_budget: number
  input_tokens: number
  reasoning_tokens: number
  output_tokens: number
  cached_tokens: number
  total_cost: number
  error_message: string | null
  heartbeat_at: string | null
}

export const screeningKeys = {
  all: ["screening"] as const,
  runs: () => [...screeningKeys.all, "runs"] as const,
  results: () => [...screeningKeys.all, "results"] as const,
  runResults: (runId: string | null) =>
    [...screeningKeys.results(), { runId }] as const,
  fundamentalDetails: () =>
    [...screeningKeys.all, "fundamental-details"] as const,
  fundamentalDetail: (resultId: string | null) =>
    [...screeningKeys.fundamentalDetails(), { resultId }] as const,
  fundamentalTraces: () =>
    [...screeningKeys.all, "fundamental-traces"] as const,
  fundamentalTrace: (resultId: string | null) =>
    [...screeningKeys.fundamentalTraces(), { resultId }] as const,
  fundamentalPass: (runId: string | null) =>
    [...screeningKeys.all, "fundamental-pass", { runId }] as const,
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

export function useFundamentalDetail(resultId: string | null) {
  return useQuery({
    queryKey: screeningKeys.fundamentalDetail(resultId),
    queryFn: () =>
      apiRequest<FundamentalDetail>(
        `/screening/results/${resultId}/fundamentals`,
      ),
    enabled: Boolean(resultId),
    staleTime: (query) => {
      const fundamentalStatus = query.state.data?.fundamental.status
      const aiStatus = query.state.data?.ai_opinion.status
      return fundamentalStatus === "queued" || fundamentalStatus === "running" ||
        aiStatus === "queued" || aiStatus === "running"
        ? 1_000
        : Number.POSITIVE_INFINITY
    },
    refetchInterval: (query) => {
      const fundamentalStatus = query.state.data?.fundamental.status
      const aiStatus = query.state.data?.ai_opinion.status
      return fundamentalStatus === "queued" || fundamentalStatus === "running" ||
        aiStatus === "queued" || aiStatus === "running" ? 1_500 : false
    },
    retry: 1,
  })
}

export function useFundamentalTrace(
  resultId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: screeningKeys.fundamentalTrace(resultId),
    queryFn: () =>
      apiRequest<FundamentalTrace>(
        `/screening/results/${resultId}/fundamentals/trace`,
      ),
    enabled: Boolean(resultId) && enabled,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })
}

export function useFundamentalPassProgress(runId: string | null) {
  return useQuery({
    queryKey: screeningKeys.fundamentalPass(runId),
    queryFn: () => apiRequest<FundamentalPassProgress | null>(`/screening/runs/${runId}/fundamental-pass`),
    enabled: Boolean(runId),
    staleTime: 1_000,
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running"
        ? 2_000
        : false,
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

export function useTriggerFundamentalPass() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ runId, mode = "retry_incomplete" }: { runId: string; mode?: "retry_incomplete" | "refresh_stale" }) =>
      apiRequest<ScanTriggerResponse>(
        `/screening/runs/${runId}/fundamental-pass`,
        { method: "POST", body: JSON.stringify({ mode }) },
      ),
    onSuccess: (_, { runId }) => {
      void queryClient.invalidateQueries({
        queryKey: screeningKeys.runResults(runId),
      })
      void queryClient.invalidateQueries({
        queryKey: screeningKeys.runs(),
      })
      void queryClient.invalidateQueries({
        queryKey: screeningKeys.fundamentalDetails(),
      })
      void queryClient.invalidateQueries({
        queryKey: screeningKeys.fundamentalTraces(),
      })
      void queryClient.invalidateQueries({ queryKey: screeningKeys.fundamentalPass(runId) })
    },
  })
}
