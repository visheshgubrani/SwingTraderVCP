import { useEffect, useMemo, useState } from "react"
import {
  CheckCircle2,
  Eye,
  FlaskConical,
  RefreshCw,
  Sparkles,
  Upload,
  XCircle,
} from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import type { ScanResult } from "@/features/screener/api"
import { VcpVisionCaptureChart } from "@/features/screener/VcpVisionCaptureChart"
import { VcpVisionOverlayChart } from "@/features/screener/VcpVisionOverlayChart"
import {
  type VcpVisionAnalysis,
  type VcpVisionVerdict,
  useCreateVcpVisionAnalysis,
  useLatestVcpVisionAnalysis,
  useRetryVcpVisionAnalysis,
  useReviewVcpVisionAnalysis,
  useUploadVcpVisionChart,
  useVcpVisionAnalysis,
  useVcpVisionStatus,
  vcpVisionChartFullUrl,
} from "@/features/screener/vcpVision"
import { ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"

function verdictClass(verdict: VcpVisionVerdict) {
  if (verdict === "valid") return "bg-green-500/15 text-green-400 border-green-500/40"
  if (verdict === "invalid") return "bg-red-500/15 text-red-400 border-red-500/40"
  return "bg-amber-500/15 text-amber-400 border-amber-500/40"
}

export function VcpVisionStatusBadge({
  status,
  aiVerdict,
  compact = false,
}: {
  status?: VcpVisionAnalysis["status"] | null
  aiVerdict?: VcpVisionVerdict | null
  compact?: boolean
}) {
  if (!status) {
    return (
      <Badge variant="outline" className="gap-1 text-[10px]">
        <Eye className="size-2.5" />
        {compact ? "" : "No VCP analysis"}
      </Badge>
    )
  }
  if (status === "succeeded" && aiVerdict) {
    return (
      <Badge
        variant="outline"
        className={cn("gap-1 text-[10px]", verdictClass(aiVerdict))}
      >
        <Sparkles className="size-2.5" />
        {aiVerdict.toUpperCase()}
      </Badge>
    )
  }
  if (status === "failed") {
    return (
      <Badge
        variant="outline"
        className="gap-1 text-[10px] border-red-500/40 text-red-400"
      >
        <XCircle className="size-2.5" />
        {compact ? "" : "FAILED"}
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="gap-1 text-[10px] animate-pulse">
      <Spinner className="size-2.5" />
      {status === "awaiting_capture"
        ? "CAPTURE"
        : status === "queued"
          ? "QUEUED"
          : "RUNNING"}
    </Badge>
  )
}

interface VcpVisionSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  result: ScanResult
  initialAnalysisId?: string | null
}

export function VcpVisionSheet({
  open,
  onOpenChange,
  result,
  initialAnalysisId,
}: VcpVisionSheetProps) {
  const [analysisId, setAnalysisId] = useState<string | null>(initialAnalysisId ?? null)
  const [captureError, setCaptureError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [showSourceImages, setShowSourceImages] = useState(false)
  const [reviewVerdict, setReviewVerdict] = useState<VcpVisionVerdict | null>(null)
  const [reviewNote, setReviewNote] = useState("")

  const createAnalysis = useCreateVcpVisionAnalysis()
  const uploadChart = useUploadVcpVisionChart()
  const retryAnalysis = useRetryVcpVisionAnalysis()
  const reviewAnalysis = useReviewVcpVisionAnalysis()
  const visionStatus = useVcpVisionStatus()

  const latestQuery = useLatestVcpVisionAnalysis(open && !analysisId ? result?.id ?? null : null)
  const detailQuery = useVcpVisionAnalysis(analysisId)
  const analysis: VcpVisionAnalysis | null = analysisId
    ? (detailQuery.data ?? null)
    : (latestQuery.data ?? null)
  const isLoading = analysisId ? detailQuery.isLoading : latestQuery.isLoading
  const noAnalysisYet =
    !analysisId &&
    latestQuery.error instanceof ApiError &&
    latestQuery.error.status === 404
  const latestLoadError =
    !analysisId && latestQuery.isError && !noAnalysisYet

  const verdict = analysis?.ai_verdict ?? null
  const humanReview = analysis?.human_review ?? null

  useEffect(() => {
    if (!open) return
    setAnalysisId(initialAnalysisId ?? null)
    setCaptureError(null)
    setShowSourceImages(false)
    setReviewVerdict(null)
    setReviewNote("")
  }, [initialAnalysisId, open, result.id])

  useEffect(() => {
    if (!humanReview) return
    setReviewVerdict(humanReview.verdict)
    setReviewNote(humanReview.note ?? "")
  }, [humanReview])

  const handleCreate = async () => {
    setCaptureError(null)
    if (visionStatus.data?.enabled === false) {
      setCaptureError("VCP vision validation is disabled on the server.")
      return
    }
    try {
      const created = await createAnalysis.mutateAsync(result?.id ?? "")
      setAnalysisId(created.analysis_id)
    } catch (err) {
      setCaptureError(err instanceof Error ? err.message : "Could not create the vision analysis.")
    }
  }

  const handleCaptured = async (contextBlob: Blob, detailBlob: Blob) => {
    if (!analysis) return
    setCaptureError(null)
    setUploading(true)
    try {
      const contextBytes = await contextBlob.arrayBuffer()
      await uploadChart.mutateAsync({
        analysisId: analysis.id,
        chart: "context",
        payload: contextBytes,
      })
      const detailBytes = await detailBlob.arrayBuffer()
      await uploadChart.mutateAsync({
        analysisId: analysis.id,
        chart: "detail",
        payload: detailBytes,
      })
    } catch (err) {
      setCaptureError(err instanceof Error ? err.message : "Chart upload failed.")
    } finally {
      setUploading(false)
    }
  }

  const handleRetry = async () => {
    if (!analysis) return
    setCaptureError(null)
    try {
      await retryAnalysis.mutateAsync(analysis.id)
    } catch (err) {
      setCaptureError(err instanceof Error ? err.message : "Retry failed.")
    }
  }

  const handleReview = async () => {
    if (!analysis || !reviewVerdict) return
    setCaptureError(null)
    try {
      await reviewAnalysis.mutateAsync({
        analysisId: analysis.id,
        verdict: reviewVerdict,
        note: reviewNote,
      })
    } catch (err) {
      setCaptureError(err instanceof Error ? err.message : "Could not save the review.")
    }
  }

  const frozen = analysis?.frozen ?? null
  const contractions = useMemo(
    () => analysis?.result?.derived.contractions ?? [],
    [analysis],
  )

  const activeStatus =
    analysis?.status === "awaiting_capture" ||
    analysis?.status === "queued" ||
    analysis?.status === "running"

  return (
    <Sheet
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) {
          setAnalysisId(initialAnalysisId ?? null)
          setCaptureError(null)
          setShowSourceImages(false)
          setReviewVerdict(null)
          setReviewNote("")
        }
      }}
      open={open}
    >
      <SheetContent className="w-full max-w-3xl gap-0 p-0 sm:max-w-3xl">
        <SheetHeader className="border-b bg-card px-4 py-3">
          <SheetTitle className="flex items-center gap-2 font-mono text-sm">
            <FlaskConical className="size-4 text-primary" />
            VCP VISION VALIDATOR
            <span className="text-muted-foreground">·</span>
            <span className="text-foreground">{result.symbol}</span>
            <VcpVisionStatusBadge
              aiVerdict={analysis?.ai_verdict ?? null}
              status={analysis?.status ?? null}
            />
          </SheetTitle>
          <SheetDescription className="font-mono text-[11px]">
            Advisory second opinion over frozen EOD candles. It never changes
            rank, eligibility, watchlists, or trade state.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 font-mono text-xs">
          {captureError && (
            <Alert variant="destructive">
              <XCircle aria-hidden="true" />
              <AlertTitle>Vision pipeline error</AlertTitle>
              <AlertDescription>{captureError}</AlertDescription>
            </Alert>
          )}

          {isLoading && (
            <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
              <Spinner />
              Loading vision analysis…
            </div>
          )}

          {!isLoading && latestLoadError && (
            <Alert variant="destructive">
              <XCircle aria-hidden="true" />
              <AlertTitle>Could not load VCP analysis</AlertTitle>
              <AlertDescription>
                {latestQuery.error instanceof Error
                  ? latestQuery.error.message
                  : "The vision analysis API is unavailable."}
              </AlertDescription>
            </Alert>
          )}

          {!isLoading && noAnalysisYet && (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <Sparkles className="size-8 stroke-1 text-muted-foreground/60" />
              <strong className="text-foreground">
                No VCP vision analysis yet
              </strong>
              <span className="max-w-md text-muted-foreground">
                This captures standardized 1280×720 context and detail charts
                from the frozen EOD window, sends them to a blind vision model,
                and returns a strict structured verdict.
              </span>
              {visionStatus.data?.enabled === false ? (
                <Alert>
                  <Eye aria-hidden="true" />
                  <AlertTitle>Vision validator is disabled</AlertTitle>
                  <AlertDescription>
                    Set VCP_VISION_ENABLED=true on the API and worker, then restart both.
                  </AlertDescription>
                </Alert>
              ) : (
                <Button
                  disabled={createAnalysis.isPending || visionStatus.isLoading}
                  onClick={() => void handleCreate()}
                  size="sm"
                  type="button"
                  className="gap-1.5"
                >
                  {createAnalysis.isPending ? <Spinner className="size-3" /> : <Sparkles className="size-3" />}
                  Run VCP vision analysis
                </Button>
              )}
            </div>
          )}

          {analysis && analysis.status === "awaiting_capture" && frozen && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Upload className="size-3" />
                Capturing standardized charts ({frozen.context_sessions}-session
                context + {frozen.detail_sessions}-session detail, log scale)…
              </div>
              {!uploading && (
                <VcpVisionCaptureChart
                  frozen={frozen}
                  onCaptured={(contextBlob, detailBlob) =>
                    void handleCaptured(contextBlob, detailBlob)
                  }
                  onError={setCaptureError}
                />
              )}
              {uploading && (
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Spinner />
                  Uploading chart images…
                </div>
              )}
            </div>
          )}

          {analysis &&
            (analysis.status === "queued" || analysis.status === "running") && (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <Spinner />
                <strong className="text-foreground">
                  Vision model is reviewing the chart
                </strong>
                <span className="text-muted-foreground">
                  {analysis.model ?? "Vision model"} · frozen window through{" "}
                  {frozen?.as_of_date ?? "—"}
                </span>
              </div>
            )}

          {analysis && analysis.status === "failed" && (
            <div className="space-y-3">
              <Alert variant="destructive">
                <XCircle aria-hidden="true" />
                <AlertTitle>Vision validation failed</AlertTitle>
                <AlertDescription>
                  {analysis.error_message ?? analysis.error_code ?? "Unknown error."}
                </AlertDescription>
              </Alert>
              <Button
                disabled={retryAnalysis.isPending}
                onClick={() => void handleRetry()}
                size="sm"
                type="button"
                variant="outline"
                className="gap-1.5"
              >
                {retryAnalysis.isPending ? <Spinner className="size-3" /> : <RefreshCw className="size-3" />}
                Retry with the same charts
              </Button>
            </div>
          )}

          {analysis && analysis.status === "succeeded" && analysis.result && (
            <>
              {analysis.candles_stale && (
                <Alert>
                  <Eye aria-hidden="true" />
                  <AlertTitle>Frozen candle source drifted</AlertTitle>
                  <AlertDescription>
                    The stored charts remain authoritative; the re-rendered
                    candle overlay may no longer match the frozen window.
                  </AlertDescription>
                </Alert>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant="outline"
                  className={cn("gap-1 text-xs", verdictClass(verdict ?? "uncertain"))}
                >
                  <Sparkles className="size-3" />
                  AI VERDICT: {(verdict ?? "uncertain").toUpperCase()}
                </Badge>
                <Badge variant="secondary" className="text-[11px]">
                  Confidence {analysis.result.confidence}%
                </Badge>
                <Badge variant="outline" className="text-[11px]">
                  {analysis.result.prior_uptrend.assessment.toUpperCase()} PRIOR
                  TREND
                </Badge>
                {analysis.result.volume && (
                  <Badge variant="outline" className="text-[11px]">
                    {analysis.result.volume.assessment.replaceAll("_", " ").toUpperCase()} VOLUME
                  </Badge>
                )}
                {humanReview?.verdict && (
                  <Badge variant="outline" className="gap-1 text-[11px]">
                    <CheckCircle2 className="size-3" />
                    HUMAN: {humanReview.verdict.toUpperCase()}
                  </Badge>
                )}
              </div>

              <p className="rounded border bg-card/60 px-3 py-2 text-foreground">
                {analysis.result.summary}
              </p>
              {analysis.result.volume?.note && (
                <p className="text-[11px] text-muted-foreground">
                  Volume: {analysis.result.volume.note}
                </p>
              )}

              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground">
                  Read-only overlay: AI contraction regions + pivot
                </span>
                <Button
                  onClick={() => setShowSourceImages((current) => !current)}
                  size="sm"
                  type="button"
                  variant="ghost"
                  className="h-7 gap-1.5 text-[11px]"
                >
                  <Eye className="size-3" />
                  {showSourceImages ? "Hide source images" : "Show source images"}
                </Button>
              </div>

              {showSourceImages ? (
                <div className="space-y-2">
                  <img
                    alt="Context chart the model received"
                    className="w-full rounded border border-border"
                    src={vcpVisionChartFullUrl(analysis.id, "context")}
                  />
                  <img
                    alt="Detail chart the model received"
                    className="w-full rounded border border-border"
                    src={vcpVisionChartFullUrl(analysis.id, "detail")}
                  />
                </div>
              ) : (
                <div className="h-80 rounded border border-border bg-[#070b12]">
                  {frozen ? (
                    <VcpVisionOverlayChart
                      contractions={contractions}
                      frozen={frozen}
                      pivotPrice={analysis.result.derived.pivot_price}
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-muted-foreground">
                      No frozen candles available
                    </div>
                  )}
                </div>
              )}

              {contractions.length > 0 && (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {contractions.map((contraction) => (
                    <div
                      className="rounded border bg-card/60 px-3 py-2"
                      key={contraction.label}
                    >
                      <span className="font-bold text-primary">
                        {contraction.label}
                      </span>
                      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
                        <span>{contraction.start}</span>
                        <span>{contraction.end}</span>
                      </div>
                      <div className="mt-1 flex justify-between text-[11px]">
                        <span className="text-foreground">
                          {contraction.sessions} sessions
                        </span>
                        <span className="text-foreground">
                          {contraction.depth_pct.toFixed(2)}% deep
                        </span>
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {contraction.high.toFixed(2)} → {contraction.low.toFixed(2)}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {analysis.result.contraction_anchors.length > 0 && (
                <div>
                  <span className="text-[11px] font-bold uppercase text-muted-foreground">
                    Contraction anchors
                  </span>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-muted-foreground">
                    {analysis.result.contraction_anchors.map((anchor) => (
                      <li key={anchor.date}>
                        <strong className="text-foreground">{anchor.date}</strong>{" "}
                        — {anchor.evidence}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {analysis.result.bases.length > 0 && (
                <div>
                  <span className="text-[11px] font-bold uppercase text-muted-foreground">
                    Base windows
                  </span>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-muted-foreground">
                    {analysis.result.bases.map((base) => (
                      <li key={`${base.start}-${base.end}`}>
                        <strong className="text-foreground">
                          {base.start} → {base.end}
                        </strong>{" "}
                        ({base.quality}){base.notes ? ` — ${base.notes}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="grid gap-2 sm:grid-cols-2">
                {analysis.result.supporting_evidence.length > 0 && (
                  <div>
                    <span className="text-[11px] font-bold uppercase text-green-500/80">
                      Supporting evidence
                    </span>
                    <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-muted-foreground">
                      {analysis.result.supporting_evidence.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {analysis.result.contrary_evidence.length > 0 && (
                  <div>
                    <span className="text-[11px] font-bold uppercase text-red-500/80">
                      Contrary evidence
                    </span>
                    <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-muted-foreground">
                      {analysis.result.contrary_evidence.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <Separator />

              <div className="space-y-2">
                <span className="text-[11px] font-bold uppercase text-muted-foreground">
                  Human review
                </span>
                <div className="flex items-center gap-2">
                  {(["valid", "invalid", "uncertain"] as const).map((option) => (
                    <button
                      className={cn(
                        "h-7 rounded border px-3 text-[11px] font-semibold transition-colors",
                        reviewVerdict === option
                          ? verdictClass(option)
                          : humanReview?.verdict === option
                            ? "border-primary bg-primary/15 text-primary"
                            : "border-border text-muted-foreground hover:bg-accent hover:text-foreground",
                      )}
                      key={option}
                      onClick={() => setReviewVerdict(option)}
                      type="button"
                    >
                      {option.toUpperCase()}
                    </button>
                  ))}
                </div>
                <Label className="text-[11px] text-muted-foreground">
                  Note (review metadata only, never prompt-training input)
                </Label>
                <Textarea
                  className="min-h-20 text-xs"
                  onChange={(e) => setReviewNote(e.target.value)}
                  placeholder="What did the chart actually show?"
                  value={reviewNote}
                />
                <Button
                  disabled={!reviewVerdict || reviewAnalysis.isPending}
                  onClick={() => void handleReview()}
                  size="sm"
                  type="button"
                  className="gap-1.5"
                >
                  {reviewAnalysis.isPending ? <Spinner className="size-3" /> : <CheckCircle2 className="size-3" />}
                  Save human review
                </Button>
              </div>
            </>
          )}

          {activeStatus && analysis && (
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Spinner className="size-3" />
              Analysis {analysis.id.slice(0, 8)} · created{" "}
              {new Date(analysis.created_at).toLocaleString("en-IN")}
            </div>
          )}
        </div>

        {analysis && analysis.status === "succeeded" && (
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t bg-card px-4 py-2 font-mono text-[11px] text-muted-foreground">
            <span>
              {analysis.model ?? "Vision model"} · {analysis.prompt_version} ·{" "}
              {analysis.renderer_version}
            </span>
            <span>
              {analysis.attempts.length} attempt(s) · cost $
              {(analysis.cost ?? 0).toFixed(6)} · input{" "}
              {String((analysis.usage as { input?: number })?.input ?? "—")} ·
              output {String((analysis.usage as { output?: number })?.output ?? "—")} · {analysis.reasoning_effort}
            </span>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
