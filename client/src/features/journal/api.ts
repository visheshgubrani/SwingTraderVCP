import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { apiRequest } from "@/lib/api"

export type JournalStatus = "open" | "closed"
export type ExecutionMode = "paper" | "live"
export type PeriodBucket = "day" | "week" | "month" | "year"

export interface JournalListItem {
  id: string
  position_id: string
  symbol: string
  execution_mode: ExecutionMode
  status: JournalStatus
  first_entry_fill_at: string | null
  first_entry_price: number | null
  first_entry_quantity: number | null
  final_entry_quantity: number | null
  closed_at: string | null
  weighted_entry_price: number | null
  weighted_exit_price: number | null
  gross_pnl: number | null
  net_pnl: number | null
  gross_r_multiple: number | null
  net_r_multiple: number | null
  hold_duration_hours: number | null
  exit_outcome: string | null
  setup_tags: string[]
  execution_rating: number | null
  charge_quality: "estimated" | "reconciled"
  pnl_mismatch: boolean
  regime: string | null
}

export interface JournalDetail extends JournalListItem {
  entry_snapshot: Record<string, unknown>
  exit_fills: Array<Record<string, unknown>>
  exit_reasons: string[]
  estimated_charges: Record<string, unknown>
  actual_charges: Record<string, unknown> | null
  risk_amount: number | null
  pnl_mismatch_delta: number | null
  notes: string | null
  mistake_tags: string[]
  emotion_tags: string[]
  lessons: string | null
  first_entry_price: number | null
  first_entry_quantity: number | null
  final_entry_quantity: number | null
  entry_frozen_at: string | null
  market_regime_snapshot_id: string | null
  reference_eod_date: string | null
  regime_evidence: Record<string, unknown>
  artifact_status: string | null
  artifact_content_hash: string | null
}

export interface JournalFilters {
  status?: JournalStatus
  execution_mode?: ExecutionMode
  symbol?: string
  setup_tag?: string
  regime?: string
  exit_outcome?: string
  offset?: number
  limit?: number
}

export interface JournalReviewUpdate {
  notes?: string | null
  execution_rating?: number | null
  setup_tags?: string[]
  mistake_tags?: string[]
  emotion_tags?: string[]
  lessons?: string | null
}

export interface PeriodSummaryRequest {
  bucket?: PeriodBucket
  execution_mode?: ExecutionMode
  symbol?: string
  setup_tag?: string
  regime?: string
  exit_outcome?: string
  date_from?: string
  date_to?: string
}

export interface ChartArtifactClaim {
  id: string
  journal_entry_id: string
  chart_source: {
    symbol: string
    candles: Array<{
      time: number
      open: number
      high: number
      low: number
      close: number
      volume: number
    }>
    entry_price?: string
    stop_loss?: string | null
    target?: string | null
  }
  capture_attempts: number
}

export interface AiCoachRun {
  id: string
  status: "queued" | "running" | "succeeded" | "failed"
  filters: Record<string, unknown>
  input_hash: string
  result: Record<string, unknown> | null
  model: string
  request_id: string | null
  usage: Record<string, unknown>
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export const journalKeys = {
  all: ["journal"] as const,
  entries: (filters: JournalFilters) =>
    [...journalKeys.all, "entries", filters] as const,
  entry: (id: string) => [...journalKeys.all, "entry", id] as const,
  summary: (payload: PeriodSummaryRequest) =>
    [...journalKeys.all, "summary", payload] as const,
  aiRuns: () => [...journalKeys.all, "ai-runs"] as const,
  aiRun: (id: string) => [...journalKeys.all, "ai-run", id] as const,
  chartUrl: (id: string) => [...journalKeys.all, "chart", id] as const,
}

export function useJournalEntries(filters: JournalFilters = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value))
    }
  })
  const query = params.toString()
  return useQuery({
    queryKey: journalKeys.entries(filters),
    queryFn: () =>
      apiRequest<{
        items: JournalListItem[]
        total: number
        offset: number
        limit: number
      }>(`/journal/entries${query ? `?${query}` : ""}`),
  })
}

export function useJournalEntry(id: string | null) {
  return useQuery({
    queryKey: journalKeys.entry(id ?? ""),
    queryFn: () => apiRequest<JournalDetail>(`/journal/entries/${id}`),
    enabled: Boolean(id),
  })
}

export function useJournalSummary(payload: PeriodSummaryRequest) {
  return useQuery({
    queryKey: journalKeys.summary(payload),
    queryFn: () =>
      apiRequest<Record<string, unknown>>("/journal/summary", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  })
}

export function useUpdateJournalReview(entryId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: JournalReviewUpdate) =>
      apiRequest<JournalDetail>(`/journal/entries/${entryId}/review`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(journalKeys.entry(entryId), data)
      queryClient.invalidateQueries({ queryKey: journalKeys.all })
    },
  })
}

export function useCreateAiCoachRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (filters: Record<string, unknown> = {}) =>
      apiRequest<AiCoachRun>("/journal/ai/runs", {
        method: "POST",
        body: JSON.stringify({ filters }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.aiRuns() })
    },
  })
}

export function useAiCoachRun(runId: string | null) {
  return useQuery({
    queryKey: journalKeys.aiRun(runId ?? ""),
    queryFn: () => apiRequest<AiCoachRun>(`/journal/ai/runs/${runId}`),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === "queued" || status === "running" ? 3000 : false
    },
  })
}

export function useAiCoachRuns() {
  return useQuery({
    queryKey: journalKeys.aiRuns(),
    queryFn: () => apiRequest<AiCoachRun[]>("/journal/ai/runs"),
  })
}

export async function claimChartArtifact(
  claimerId: string,
): Promise<ChartArtifactClaim | null> {
  return apiRequest<ChartArtifactClaim | null>(
    `/journal/artifacts/claim?claimer_id=${encodeURIComponent(claimerId)}`,
    { method: "POST" },
  )
}

export async function uploadChartArtifact(
  artifactId: string,
  claimerId: string,
  pngBlob: Blob,
): Promise<{ id: string; journal_entry_id: string; content_hash: string }> {
  return apiRequest(`/journal/artifacts/${artifactId}/upload?claimer_id=${encodeURIComponent(claimerId)}`, {
    method: "PUT",
    headers: { "Content-Type": "image/png" },
    body: pngBlob,
  })
}

export function journalChartUrl(entryId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1"
  return `${base}/journal/entries/${entryId}/chart.png`
}
