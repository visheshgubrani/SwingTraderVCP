import { useState } from "react"
import { useNavigate, useParams } from "react-router"
import {
  ArrowLeftIcon,
  CrosshairIcon,
  Maximize2Icon,
  ShieldAlertIcon,
  SparklesIcon,
  TargetIcon,
  XCircleIcon,
} from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { useProposalAttempt } from "./api"

export function ProposalAttemptDetailPage() {
  const { attemptId } = useParams<{ attemptId: string }>()
  const navigate = useNavigate()
  const [activeChartTab, setActiveChartTab] = useState<"both" | "detail" | "context">("both")
  const [imageModal, setImageModal] = useState<string | null>(null)

  const { data: attempt, isLoading, error } = useProposalAttempt(attemptId ?? null)

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-background text-foreground font-mono">
        <div className="flex flex-col items-center gap-3">
          <Spinner className="h-6 w-6 text-primary" />
          <p className="text-xs text-muted-foreground">Loading rejected trade attempt details…</p>
        </div>
      </div>
    )
  }

  if (error || !attempt) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-4 bg-background p-6 font-mono text-foreground">
        <Alert variant="destructive" className="max-w-md">
          <ShieldAlertIcon className="h-4 w-4" />
          <AlertTitle>Attempt Record Not Found</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : "The requested trade candidate attempt could not be found."}
          </AlertDescription>
        </Alert>
        <Button variant="outline" size="sm" onClick={() => navigate("/proposals")}>
          <ArrowLeftIcon className="mr-1.5 h-3.5 w-3.5" /> Back to Proposals
        </Button>
      </div>
    )
  }

  const structured = attempt.structured_output ?? {}
  const geminiPivot = structured.pivot_price != null ? Number(structured.pivot_price) : null
  const geminiT1 = structured.t1 != null ? Number(structured.t1) : null
  const geminiT2 = structured.t2 != null ? Number(structured.t2) : null
  const geminiT3 = structured.t3 != null ? Number(structured.t3) : null
  const anchors = Array.isArray(structured.contraction_anchors) ? structured.contraction_anchors : []
  const confidence = structured.confidence != null ? Number(structured.confidence) : null
  const verdict = structured.verdict ?? "unknown"
  const entryTemplate = (structured.entry_template as string) || "single"

  const contextChartSrc = `/api/v1/automation/attempts/${attempt.id}/charts/context`
  const detailChartSrc = `/api/v1/automation/attempts/${attempt.id}/charts/detail`

  return (
    <div className="flex h-full w-full flex-col overflow-y-auto bg-background font-mono text-xs text-foreground">
      {/* Top Header */}
      <div className="sticky top-0 z-20 flex shrink-0 items-center justify-between border-b border-border/80 bg-card/95 px-4 py-3 backdrop-blur shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1 px-2.5 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => navigate("/proposals")}
          >
            <ArrowLeftIcon className="h-3.5 w-3.5" /> Proposals
          </Button>

          <span className="text-border">|</span>

          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold tracking-tight text-foreground">
              {attempt.symbol}
            </h1>
            <Badge variant="destructive" className="font-bold uppercase tracking-wider">
              {attempt.status === "invalid" ? "REJECTED BY SYSTEM" : attempt.status.toUpperCase()}
            </Badge>
            {verdict && (
              <Badge variant="outline" className="text-[10px]">
                Gemini {String(verdict).toUpperCase()}
              </Badge>
            )}
            {confidence != null && (
              <Badge variant="outline" className="text-[10px]">
                CONF: {(confidence * 100).toFixed(0)}%
              </Badge>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden text-right text-[11px] text-muted-foreground sm:block">
            <div>Attempt: <strong className="text-foreground">#{attempt.attempt_number}</strong></div>
            <div className="text-[10px]">
              {new Date(attempt.started_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST
            </div>
          </div>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="mx-auto w-full max-w-7xl space-y-6 p-4 sm:p-6">
        {/* Rejection Cause Alert Banner */}
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 shadow-sm">
          <div className="flex items-start gap-3">
            <XCircleIcon className="h-5 w-5 shrink-0 text-rose-400 mt-0.5" />
            <div className="space-y-1">
              <div className="text-sm font-bold text-rose-300">
                System Rejection: {attempt.error_type || "Validation Rule Violation"}
              </div>
              <p className="text-[11px] leading-relaxed text-rose-200">
                {attempt.error_message || "This candidate setup was deterministically rejected by Python risk and geometry rules."}
              </p>
              {attempt.error_details && Object.keys(attempt.error_details).length > 0 && (
                <div className="mt-2 rounded bg-black/40 p-2 text-[10px] text-muted-foreground">
                  <span className="font-semibold text-rose-400">Error Details: </span>
                  {JSON.stringify(attempt.error_details)}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Proposed Levels Summary Dashboard (if Gemini suggested levels) */}
        {geminiPivot != null && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
              <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
                <span>Proposed Pivot</span>
                <CrosshairIcon className="h-3 w-3 text-primary" />
              </div>
              <div className="mt-1 text-base font-bold text-foreground">
                ₹{geminiPivot.toFixed(2)}
              </div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">
                Suggested breakout level
              </div>
            </div>

            <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
              <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
                <span>Proposed Target 1</span>
                <TargetIcon className="h-3 w-3 text-emerald-400" />
              </div>
              <div className="mt-1 text-base font-bold text-emerald-400">
                {geminiT1 != null ? `₹${geminiT1.toFixed(2)}` : "-"}
              </div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">
                Primary upside target
              </div>
            </div>

            <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
              <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
                <span>Proposed Target 2</span>
                <TargetIcon className="h-3 w-3 text-emerald-400/80" />
              </div>
              <div className="mt-1 text-base font-bold text-foreground">
                {geminiT2 != null ? `₹${geminiT2.toFixed(2)}` : "-"}
              </div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">
                Secondary expansion target
              </div>
            </div>

            <div className="rounded-lg border border-border/70 bg-card p-3 shadow-sm">
              <div className="flex items-center justify-between text-[10px] uppercase text-muted-foreground">
                <span>Proposed Target 3</span>
                <TargetIcon className="h-3 w-3 text-emerald-400/60" />
              </div>
              <div className="mt-1 text-base font-bold text-foreground">
                {geminiT3 != null ? `₹${geminiT3.toFixed(2)}` : "-"}
              </div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">
                Runner swing target
              </div>
            </div>
          </div>
        )}

        {/* High-Resolution Headless Rendered Charts */}
        <div className="rounded-xl border border-border/70 bg-card overflow-hidden shadow-sm">
          <div className="flex flex-wrap items-center justify-between border-b border-border/60 bg-muted/20 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <SparklesIcon className="h-4 w-4 text-primary" />
              <span className="font-semibold text-foreground uppercase tracking-wider text-[11px]">
                Deterministic Candidate Charts
              </span>
              <Badge variant="outline" className="text-[10px]">
                {attempt.renderer_version}
              </Badge>
            </div>

            <div className="flex items-center gap-1.5">
              <Button
                variant={activeChartTab === "both" ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setActiveChartTab("both")}
              >
                Both Charts
              </Button>
              <Button
                variant={activeChartTab === "detail" ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setActiveChartTab("detail")}
              >
                Detail (126s)
              </Button>
              <Button
                variant={activeChartTab === "context" ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setActiveChartTab("context")}
              >
                Context (252s)
              </Button>
            </div>
          </div>

          <div className={`p-4 grid gap-4 ${activeChartTab === "both" ? "lg:grid-cols-2" : "grid-cols-1"}`}>
            {(activeChartTab === "both" || activeChartTab === "context") && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
                  <span>Context View · 252 Sessions (Log Scale + 50/150/200 SMA)</span>
                  <button
                    type="button"
                    onClick={() => setImageModal(contextChartSrc)}
                    className="hover:text-foreground text-muted-foreground flex items-center gap-1"
                  >
                    <Maximize2Icon className="h-3 w-3" /> Expand
                  </button>
                </div>
                <div className="relative group overflow-hidden rounded-lg border border-border/60 bg-black">
                  <img
                    src={contextChartSrc}
                    alt={`${attempt.symbol} 252-session context`}
                    className="w-full object-contain cursor-pointer transition-transform duration-200 group-hover:scale-[1.01]"
                    onClick={() => setImageModal(contextChartSrc)}
                  />
                </div>
              </div>
            )}

            {(activeChartTab === "both" || activeChartTab === "detail") && (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
                  <span>Inference Detail · 126 Sessions (Annotated VCP Geometry + 21 EMA)</span>
                  <button
                    type="button"
                    onClick={() => setImageModal(detailChartSrc)}
                    className="hover:text-foreground text-muted-foreground flex items-center gap-1"
                  >
                    <Maximize2Icon className="h-3 w-3" /> Expand
                  </button>
                </div>
                <div className="relative group overflow-hidden rounded-lg border border-border/60 bg-black">
                  <img
                    src={detailChartSrc}
                    alt={`${attempt.symbol} 126-session detail`}
                    className="w-full object-contain cursor-pointer transition-transform duration-200 group-hover:scale-[1.01]"
                    onClick={() => setImageModal(detailChartSrc)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Gemini Vision AI Evidence & Extrema Anchors */}
        <div className="rounded-xl border border-border/70 bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between border-b border-border/50 pb-2">
            <span className="flex items-center gap-2 font-bold text-foreground text-sm uppercase tracking-wider">
              <TargetIcon className="h-4 w-4 text-primary" /> Gemini VCP Vision Output & Evidence
            </span>
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-muted-foreground">Model:</span>
              <strong className="text-foreground">{attempt.model}</strong>
            </div>
          </div>

          <div className="mt-3 grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2 space-y-3">
              <div className="flex flex-wrap gap-2 text-[11px]">
                <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-1.5">
                  <span className="text-muted-foreground">Template: </span>
                  <strong className="text-foreground capitalize">{entryTemplate.replace("_", " ")}</strong>
                </div>
                <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-1.5">
                  <span className="text-muted-foreground">Base Tightness: </span>
                  <strong className="text-foreground capitalize">{String(structured.base_tightness ?? "Not specified")}</strong>
                </div>
                <div className="rounded-md border border-border/40 bg-muted/20 px-3 py-1.5">
                  <span className="text-muted-foreground">Volume Dry-Up: </span>
                  <strong className="text-foreground capitalize">{String(structured.dry_up_quality ?? "Not specified")}</strong>
                </div>
              </div>

              <div className="rounded-lg border border-border/40 bg-background/50 p-3 text-[11px] leading-relaxed text-muted-foreground">
                <div className="mb-1 text-[10px] uppercase font-semibold text-foreground">AI Evidence Summary</div>
                {String(structured.evidence_summary || "No structured summary returned.")}
              </div>
            </div>

            {/* Pattern Anchors Table */}
            <div className="rounded-lg border border-border/40 bg-background/50 p-3">
              <div className="mb-2 text-[10px] uppercase font-semibold text-foreground">Pattern Anchors</div>
              {anchors.length > 0 ? (
                <div className="max-h-40 overflow-y-auto">
                  <table className="w-full text-left text-[10px]">
                    <thead>
                      <tr className="border-b border-border/40 text-muted-foreground">
                        <th className="py-1">Date</th>
                        <th className="py-1">Type</th>
                        <th className="py-1 text-right">Price</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/20">
                      {anchors.map((anchor: any, idx: number) => (
                        <tr key={idx}>
                          <td className="py-1 font-mono text-muted-foreground">{anchor.date}</td>
                          <td className="py-1 text-foreground capitalize">
                            {anchor.anchor_type?.replace("_", " ") ?? "Anchor"}
                          </td>
                          <td className="py-1 text-right font-mono font-semibold text-foreground">
                            ₹{Number(anchor.price ?? 0).toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-[10px] text-muted-foreground">No pattern anchors recorded.</div>
              )}
            </div>
          </div>
        </div>

        {/* Audit & Hash Metadata Footer */}
        <div className="rounded-lg border border-border/40 bg-muted/10 p-3 text-[10px] text-muted-foreground">
          <div className="flex flex-wrap items-center justify-between gap-2 font-mono">
            <div>Attempt ID: <span className="text-foreground">{attempt.id}</span></div>
            <div>Source Hash: <span className="text-foreground">{attempt.source_hash.slice(0, 16)}…</span></div>
            <div>Risk Policy Version: <span className="text-foreground">v{attempt.risk_policy_version}</span></div>
            <div>Duration: {attempt.completed_at ? `${((new Date(attempt.completed_at).getTime() - new Date(attempt.started_at).getTime()) / 1000).toFixed(1)}s` : "-"}</div>
          </div>
        </div>
      </div>

      {/* Lightbox Image Modal */}
      {imageModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={() => setImageModal(null)}
        >
          <div className="relative max-h-[95vh] max-w-[95vw] overflow-hidden rounded-xl border border-border/80 bg-card p-2 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between px-2">
              <span className="text-xs font-semibold text-foreground">Attempt Chart Preview</span>
              <Button size="xs" variant="ghost" onClick={() => setImageModal(null)}>Close ✕</Button>
            </div>
            <img src={imageModal} alt="Expanded attempt chart" className="max-h-[85vh] w-auto rounded-lg object-contain bg-black" />
          </div>
        </div>
      )}
    </div>
  )
}
