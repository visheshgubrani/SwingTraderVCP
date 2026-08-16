import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { screeningKeys } from "@/features/screener/api"
import { ApiError, apiRequest } from "@/lib/api"

export type VcpVisionStatus =
  | "awaiting_capture"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"

export type VcpVisionVerdict = "valid" | "invalid" | "uncertain"

export interface VcpVisionCandle {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface VcpVisionFrozen {
  symbol: string | null
  as_of_date: string
  context_sessions: number
  detail_sessions: number
  source_hash: string
  candles: VcpVisionCandle[]
}

export interface VcpVisionContraction {
  label: string
  start: string
  end: string
  high: number
  low: number
  depth_pct: number
  sessions: number
}

export interface VcpVisionResult {
  schema_version: string
  verdict: VcpVisionVerdict
  confidence: number
  summary: string
  prior_uptrend: {
    assessment: "clear" | "moderate" | "weak" | "unclear"
    note: string
  }
  volume?: {
    assessment: "drying_up" | "supportive" | "mixed" | "weak" | "unclear"
    note: string
  }
  bases: Array<{
    start: string
    end: string
    quality: "solid" | "loose" | "unclear"
    notes: string
  }>
  contraction_anchors: Array<{
    date: string
    evidence: string
  }>
  pivot_zone: {
    start: string
    end: string
    rationale: string
  } | null
  supporting_evidence: string[]
  contrary_evidence: string[]
  human_review_focus: string[]
  derived: {
    contractions: VcpVisionContraction[]
    pivot_price: number | null
  }
}

export interface VcpVisionAttempt {
  id: string
  attempt_number: number
  status: string
  model: string
  reasoning_effort: string
  prompt_version: string
  input_hash: string
  request_id: string | null
  http_status: number | null
  usage: Record<string, unknown>
  cost: number
  error_code: string | null
  error_message: string | null
  started_at: string
  completed_at: string | null
}

export interface VcpVisionAnalysis {
  id: string
  screening_result_id: string
  status: VcpVisionStatus
  chart_source: {
    as_of_date?: string
    symbol?: string
    context_sessions?: number
    detail_sessions?: number
    [key: string]: unknown
  }
  renderer_version: string
  model: string | null
  reasoning_effort: string
  max_tokens: number
  prompt_version: string
  schema_version: string
  result: VcpVisionResult | null
  ai_verdict: VcpVisionVerdict | null
  error_code: string | null
  error_message: string | null
  usage: Record<string, unknown>
  cost: number
  human_review: {
    verdict: VcpVisionVerdict | null
    note: string | null
    reviewed_at: string | null
  } | null
  created_at: string
  updated_at: string
  attempts: VcpVisionAttempt[]
  frozen: VcpVisionFrozen | null
  candles_stale: boolean
}

export interface VcpVisionSummary {
  id: string
  status: VcpVisionStatus
  ai_verdict: VcpVisionVerdict | null
  human_verdict: VcpVisionVerdict | null
  created_at: string | null
  error_code: string | null
}

export interface VcpVisionCreateResponse {
  analysis_id: string
  status: VcpVisionStatus
  reused: boolean
  message: string
}

export interface VcpVisionChartUploadResponse {
  analysis_id: string
  chart: "context" | "detail"
  status: "awaiting_capture" | "queued"
  message: string
}

export interface VcpVisionStatusResponse {
  enabled: boolean
  model: string | null
  counts: Record<string, number>
}

export const vcpVisionKeys = {
  all: ["vcp-vision"] as const,
  status: () => [...vcpVisionKeys.all, "status"] as const,
  analyses: () => [...vcpVisionKeys.all, "analyses"] as const,
  analysis: (analysisId: string | null) =>
    [...vcpVisionKeys.analyses(), { analysisId }] as const,
  latest: (resultId: string | null) =>
    [...vcpVisionKeys.all, "latest", { resultId }] as const,
}

export function vcpVisionChartUrl(
  analysisId: string,
  chart: "context" | "detail",
) {
  return `/screening/vcp-vision/analyses/${analysisId}/charts/${chart}`
}

export function vcpVisionChartFullUrl(
  analysisId: string,
  chart: "context" | "detail",
) {
  return `${import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1"}${vcpVisionChartUrl(analysisId, chart)}`
}

export function useVcpVisionStatus() {
  return useQuery({
    queryKey: vcpVisionKeys.status(),
    queryFn: () => apiRequest<VcpVisionStatusResponse>("/screening/vcp-vision/status"),
    staleTime: 30_000,
    retry: false,
  })
}

export function useCreateVcpVisionAnalysis() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (resultId: string) =>
      apiRequest<VcpVisionCreateResponse>(
        `/screening/results/${resultId}/vcp-vision/analyses`,
        { method: "POST" },
      ),
    onSuccess: (_created, resultId) => {
      void queryClient.invalidateQueries({
        queryKey: vcpVisionKeys.latest(resultId),
      })
      void queryClient.invalidateQueries({ queryKey: screeningKeys.results() })
    },
  })
}

export function useUploadVcpVisionChart() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      analysisId,
      chart,
      payload,
    }: {
      analysisId: string
      chart: "context" | "detail"
      payload: ArrayBuffer
    }) =>
      apiRequest<VcpVisionChartUploadResponse>(
        vcpVisionChartUrl(analysisId, chart),
        {
          method: "PUT",
          headers: { "Content-Type": "application/octet-stream" },
          body: new Blob([payload]),
        },
      ),
    onSuccess: (uploaded) => {
      // The first image leaves the row in awaiting_capture. Refetching there
      // recreates the hidden capture chart while its second upload is still
      // in flight and can start a duplicate capture. Only the upload that
      // actually transitions the row to queued needs to refresh consumers.
      if (uploaded.status === "queued") {
        void queryClient.invalidateQueries({
          queryKey: vcpVisionKeys.analysis(uploaded.analysis_id),
        })
        void queryClient.invalidateQueries({ queryKey: screeningKeys.results() })
      }
    },
  })
}

function analysisIsActive(status?: VcpVisionStatus) {
  return status === "queued" || status === "running"
}

export function useVcpVisionAnalysis(analysisId: string | null) {
  return useQuery({
    queryKey: vcpVisionKeys.analysis(analysisId),
    queryFn: () =>
      apiRequest<VcpVisionAnalysis>(
        `/screening/vcp-vision/analyses/${analysisId}`,
      ),
    enabled: Boolean(analysisId),
    staleTime: (query) => (analysisIsActive(query.state.data?.status) ? 1_000 : Number.POSITIVE_INFINITY),
    refetchInterval: (query) => (analysisIsActive(query.state.data?.status) ? 1_500 : false),
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 1,
  })
}

export function useLatestVcpVisionAnalysis(resultId: string | null) {
  return useQuery({
    queryKey: vcpVisionKeys.latest(resultId),
    queryFn: () =>
      apiRequest<VcpVisionAnalysis>(
        `/screening/results/${resultId}/vcp-vision/latest`,
      ),
    enabled: Boolean(resultId),
    staleTime: (query) => (analysisIsActive(query.state.data?.status) ? 1_000 : Number.POSITIVE_INFINITY),
    refetchInterval: (query) => (analysisIsActive(query.state.data?.status) ? 1_500 : false),
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status === 404) && failureCount < 1,
  })
}

export function useReviewVcpVisionAnalysis() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      analysisId,
      verdict,
      note,
    }: {
      analysisId: string
      verdict: VcpVisionVerdict
      note: string
    }) =>
      apiRequest<VcpVisionAnalysis>(
        `/screening/vcp-vision/analyses/${analysisId}/review`,
        { method: "PATCH", body: JSON.stringify({ verdict, note }) },
      ),
    onSuccess: (analysis) => {
      void queryClient.invalidateQueries({
        queryKey: vcpVisionKeys.analysis(analysis.id),
      })
      void queryClient.invalidateQueries({ queryKey: screeningKeys.results() })
    },
  })
}

export function useRetryVcpVisionAnalysis() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (analysisId: string) =>
      apiRequest<VcpVisionChartUploadResponse>(
        `/screening/vcp-vision/analyses/${analysisId}/retry`,
        { method: "POST" },
      ),
    onSuccess: (uploaded) => {
      void queryClient.invalidateQueries({
        queryKey: vcpVisionKeys.analysis(uploaded.analysis_id),
      })
      void queryClient.invalidateQueries({ queryKey: screeningKeys.results() })
    },
  })
}
