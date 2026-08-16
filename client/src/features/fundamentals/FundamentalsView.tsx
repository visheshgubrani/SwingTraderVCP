import { useEffect, useMemo, useState } from "react"
import {
  AlertTriangleIcon,
  BrainCircuitIcon,
  Building2Icon,
  CheckCircle2Icon,
  ChevronDownIcon,
  PauseCircleIcon,
  PlayCircleIcon,
  RotateCwIcon,
  SearchIcon,
  ShieldAlertIcon,
  SparklesIcon,
  TargetIcon,
  TrendingUpIcon,
  XCircleIcon,
} from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Spinner } from "@/components/ui/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  type FundamentalDetail,
  type FundamentalAssessment,
  type FundamentalHistoryPoint,
  type ScanResult,
  type ScanRun,
  useFundamentalDetail,
  useFundamentalPassProgress,
  useFundamentalTrace,
  useScanResults,
  useScanRuns,
  useTriggerFundamentalPass,
} from "@/features/screener/api"
import { useFundamentalControls, useSetFundamentalControl } from "@/features/admin/api"
import { cn } from "@/lib/utils"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"

type FitFilter =
  | "all"
  | "A"
  | "B"
  | "C"
  | "D"
  | "insufficient"
  | "processing"
  | "unavailable"

type SortMode = "fundamental" | "technical"

function formatRun(run: ScanRun) {
  const date = new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(run.created_at))
  return `${date} · ${run.passing_count} setups`
}

function fitBadge(result: Pick<ScanResult, "fundamental_assessment" | "fundamental_status">) {
  if (result.fundamental_status === "queued" || result.fundamental_status === "running") {
    return <Badge variant="secondary">Processing</Badge>
  }
  if (result.fundamental_status === "failed") {
    return <Badge variant="destructive">Data unavailable</Badge>
  }
  if (result.fundamental_status === "not_requested") {
    return <Badge variant="outline">Not selected</Badge>
  }
  if (result.fundamental_status === "skipped") {
    return <Badge variant="outline">Unavailable</Badge>
  }
  const grade = result.fundamental_assessment?.grade
  if (grade === "insufficient") return <Badge variant="outline">Insufficient data</Badge>
  if (grade === "A") return <Badge className="bg-emerald-600 text-white font-semibold">Fit A</Badge>
  if (grade === "B") return <Badge className="bg-blue-600 text-white font-semibold">Fit B</Badge>
  if (grade === "C") return <Badge variant="outline">Fit C</Badge>
  if (grade === "D") return <Badge variant="destructive">Fit D</Badge>
  return <Badge variant="outline">Awaiting analysis</Badge>
}

function aiVerdictBadge(verdict: string | null, status: string) {
  if (verdict === "pass") {
    return <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white font-bold px-2.5 py-0.5">PASS</Badge>
  }
  if (verdict === "fail") {
    return <Badge className="bg-red-600 hover:bg-red-600 text-white font-bold px-2.5 py-0.5">FAIL</Badge>
  }
  if (verdict === "uncertain") {
    return <Badge className="bg-amber-600 hover:bg-amber-600 text-white font-bold px-2.5 py-0.5">UNCERTAIN</Badge>
  }
  return <Badge variant="outline">{status.replaceAll("_", " ")}</Badge>
}

function matchesFilter(result: ScanResult, filter: FitFilter) {
  if (filter === "all") return true
  if (filter === "processing") {
    return result.fundamental_status === "queued" || result.fundamental_status === "running"
  }
  if (filter === "unavailable") {
    return (
      result.fundamental_status === "failed" ||
      result.fundamental_status === "skipped" ||
      result.fundamental_status === "not_requested"
    )
  }
  return result.fundamental_assessment?.grade === filter
}

function keyRedFlags(result: ScanResult) {
  return result.fundamental_assessment?.red_flags?.slice(0, 2) ?? []
}

function scoreLabel(assessment: FundamentalAssessment | null) {
  if (!assessment) return "—"
  if (assessment.score === null) return "—"
  return `${assessment.score.toFixed(0)}/100`
}

function strongestFactor(assessment: FundamentalAssessment | null) {
  if (!assessment) return "—"
  const component = [...assessment.components]
    .filter((item) => item.max_points > 0 && item.available_points > 0)
    .sort((left, right) =>
      right.earned_points / right.available_points - left.earned_points / left.available_points,
    )[0]
  return component ? prettyKey(component.name) : "—"
}

function prettyKey(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) =>
    letter.toUpperCase(),
  )
}

function formatHistoryValue(item: FundamentalHistoryPoint) {
  const value = item.value_pct ?? item.value
  return typeof value === "number"
    ? value.toLocaleString("en-IN", { maximumFractionDigits: 2 })
    : "—"
}

function HistoryTable({
  title,
  histories,
}: {
  title: string
  histories?: Record<string, FundamentalHistoryPoint[] | null>
}) {
  const rows = Object.entries(histories ?? {}).filter(
    ([, points]) => points && points.length > 0,
  )
  if (!rows.length) return null

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Metric</th>
              <th className="px-3 py-2">Historical Points</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {rows.map(([name, points]) => (
              <tr key={name}>
                <th className="px-3 py-2 font-medium">{prettyKey(name)}</th>
                <td className="px-3 py-2 font-mono text-muted-foreground">
                  {points!
                    .map(
                      (point) =>
                        `${point.period}: ${formatHistoryValue(point)}`,
                    )
                    .join(" · ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

type TraceStage = "upstox" | "normalized" | "rules" | "request" | "response"

function FundamentalPipelineTrace({
  detail,
}: {
  detail: FundamentalDetail
}) {
  const [opened, setOpened] = useState(false)
  const [stage, setStage] = useState<TraceStage>("upstox")
  const traceQuery = useFundamentalTrace(detail.result_id, opened)
  const trace = traceQuery.data
  const latestAttempt = trace?.ai_attempts.at(-1)
  const stages: Array<{ id: TraceStage; label: string; status: string }> = [
    { id: "upstox", label: "Upstox", status: trace?.source.snapshot_id ? "captured" : "unavailable" },
    { id: "normalized", label: "Normalized", status: trace?.normalized.schema_version ? "validated" : "unavailable" },
    { id: "rules", label: "Python fit", status: trace?.python_fit.contract_valid ? "valid" : "invalid" },
    { id: "request", label: "AI request", status: trace?.ai_request ? "captured" : "not captured" },
    { id: "response", label: "AI response", status: latestAttempt?.status ?? "not captured" },
  ]
  const selectedPayload = trace
    ? stage === "upstox"
      ? trace.source
      : stage === "normalized"
        ? trace.normalized
        : stage === "rules"
          ? trace.python_fit
          : stage === "request"
            ? trace.ai_request
            : {
                legacy_response_captured: trace.legacy_response_captured,
                attempts: trace.ai_attempts,
              }
    : null

  if (!opened) {
    return (
      <details className="group rounded-lg border border-dashed p-3">
        <summary className="cursor-pointer text-xs font-semibold text-muted-foreground hover:text-foreground flex items-center justify-between" onClick={(e) => { e.preventDefault(); setOpened(true) }}>
          <span>Developer Audit Trace (Upstox, Python Contract & AI Payloads)</span>
          <ChevronDownIcon className="size-4 transition-transform group-open:rotate-180" />
        </summary>
      </details>
    )
  }

  return (
    <section className="flex flex-col gap-3 rounded-lg border p-4 bg-muted/20">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">Developer Audit Trace</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Upstox → Normalized → Python fit → AI request → AI response
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpened(false)}>Hide trace</Button>
      </div>
      {traceQuery.isLoading ? (
        <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
          <Spinner className="size-3.5" /> Loading audit trace payload…
        </div>
      ) : traceQuery.isError ? (
        <Alert variant="destructive">
          <XCircleIcon aria-hidden="true" />
          <AlertTitle>Could not load trace</AlertTitle>
          <AlertDescription>
            {traceQuery.error instanceof Error ? traceQuery.error.message : "Trace unavailable."}
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <ToggleGroup className="flex w-full flex-wrap justify-start" size="sm" variant="outline">
            {stages.map((item) => (
              <ToggleGroupItem
                aria-label={`Show ${item.label} trace`}
                key={item.id}
                onPressedChange={(pressed) => pressed && setStage(item.id)}
                pressed={stage === item.id}
              >
                <span>{item.label}</span>
                <Badge
                  className="ml-1 text-[10px]"
                  variant={
                    ["invalid", "invalid_response", "provider_error", "transport_unknown", "unavailable"].includes(item.status)
                      ? "destructive"
                      : item.status === "not captured"
                        ? "outline"
                        : "secondary"
                  }
                >
                  {item.status}
                </Badge>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <pre className="max-h-80 overflow-auto rounded-lg border bg-background p-3 text-[11px] font-mono whitespace-pre-wrap">
            {JSON.stringify(selectedPayload, null, 2)}
          </pre>
        </>
      )}
    </section>
  )
}

function FundamentalInspector({ detail }: { detail: FundamentalDetail }) {
  const verdict = detail.ai_opinion.verdict
  const assessment = detail.fundamental.assessment
  const facts = detail.snapshot?.normalized_facts
  const holdings = Object.entries(facts?.histories?.shareholding ?? {})
  const pledgeRisk = detail.risk_checks.promoter_pledge
  const leverageRisk = detail.risk_checks.leverage
  const filingScoreImpact = assessment?.risk_score_impact ?? 0

  const riskLabel = (status?: string) =>
    status === "not_applicable" ? "Not applicable" : prettyKey(status ?? "unknown")

  const riskBadgeVariant = (status?: string) =>
    status === "red" || status === "severe" ? "destructive" as const : "outline" as const

  const riskAlertVariant = (status?: string) =>
    status === "red" || status === "severe" ? "destructive" as const : "default" as const

  const verdictMeta = {
    pass: {
      bg: "bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-300",
      badgeClass: "bg-emerald-600 hover:bg-emerald-600 text-white font-black text-sm tracking-wider px-3 py-1",
      icon: CheckCircle2Icon,
      label: "AI VERDICT: PASS",
      desc: "Fundamental evidence aligns with SEPA/Minervini growth criteria.",
    },
    fail: {
      bg: "bg-red-500/10 border-red-500/30 text-red-700 dark:text-red-300",
      badgeClass: "bg-red-600 hover:bg-red-600 text-white font-black text-sm tracking-wider px-3 py-1",
      icon: XCircleIcon,
      label: "AI VERDICT: FAIL",
      desc: "Severe fundamental weakness, revenue/profit contraction, or margin erosion detected.",
    },
    uncertain: {
      bg: "bg-amber-500/10 border-amber-500/30 text-amber-700 dark:text-amber-300",
      badgeClass: "bg-amber-600 hover:bg-amber-600 text-white font-black text-sm tracking-wider px-3 py-1",
      icon: AlertTriangleIcon,
      label: "AI VERDICT: UNCERTAIN",
      desc: "Financial data is sparse or quarterly/annual signals are conflicting.",
    },
  }[verdict ?? "uncertain"]

  const VerdictIcon = verdictMeta.icon

  return (
    <div className="flex flex-col gap-6 px-5 py-6">
      {/* 1. Clear-Cut AI Verdict Banner */}
      <section className={cn("flex flex-col gap-3 rounded-xl border p-5 shadow-sm transition-all", verdictMeta.bg)}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <VerdictIcon className="size-7 shrink-0" />
            <div>
              <div className="flex items-center gap-2">
                <Badge className={verdictMeta.badgeClass}>{verdictMeta.label}</Badge>
                {assessment && (
                  <Badge className={cn(assessment.grade === "A" ? "bg-emerald-700 text-white" : assessment.grade === "B" ? "bg-blue-700 text-white" : "bg-muted text-muted-foreground")}>
                    Python Grade {assessment.grade} ({scoreLabel(assessment)})
                  </Badge>
                )}
              </div>
              <p className="mt-1 text-xs font-medium opacity-90">{verdictMeta.desc}</p>
            </div>
          </div>
          {detail.snapshot && (
            <div className="text-right text-xs text-muted-foreground">
              <div>Provider: <span className="font-semibold text-foreground uppercase">{detail.snapshot.provider}</span></div>
              <div>Statement: <span className="font-semibold text-foreground capitalize">{detail.snapshot.statement_type}</span></div>
            </div>
          )}
        </div>

        <div className="mt-2 rounded-lg bg-background/80 p-4 border shadow-2xs backdrop-blur-xs">
          <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
            <SparklesIcon className="size-4 text-amber-500" />
            <span>AI Executive Summary</span>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-foreground">
            {detail.ai_opinion.summary ??
              "Deterministic Python fit score is available. Independent AI qualitative summary is currently pending or skipped."}
          </p>
        </div>
      </section>

      {/* Deterministic India-specific filing checks adjust only the fundamental fit. */}
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Filing risk checks
            </p>
            <p className="text-sm text-muted-foreground">
              Official NSE filings · fundamental score impact {filingScoreImpact.toFixed(0)} · never auto-rejects
            </p>
          </div>
          <Badge variant="secondary">{detail.source_snapshots.length} source snapshots</Badge>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <Alert variant={riskAlertVariant(pledgeRisk?.status)}>
            <ShieldAlertIcon />
            <AlertTitle className="flex items-center justify-between gap-2">
              <span>Promoter pledge</span>
              <Badge variant={riskBadgeVariant(pledgeRisk?.status)}>
                {riskLabel(pledgeRisk?.status)}
              </Badge>
            </AlertTitle>
            <AlertDescription>
              {typeof pledgeRisk?.value === "number"
                ? `${pledgeRisk.value.toFixed(2)}% of promoter-group holdings are pledged · ${pledgeRisk.score_impact ?? 0} points.`
                : "The current filing is missing or ambiguous; do not interpret this as zero pledge."}
            </AlertDescription>
          </Alert>

          <Alert variant={riskAlertVariant(leverageRisk?.status)}>
            <AlertTriangleIcon />
            <AlertTitle className="flex items-center justify-between gap-2">
              <span>Balance-sheet leverage</span>
              <Badge variant={riskBadgeVariant(leverageRisk?.status)}>
                {riskLabel(leverageRisk?.status)}
              </Badge>
            </AlertTitle>
            <AlertDescription>
              {leverageRisk?.status === "not_applicable"
                ? "Industrial leverage thresholds are not applied to financial businesses."
                : typeof leverageRisk?.debt_to_equity === "number" ||
                    typeof leverageRisk?.interest_service_coverage === "number"
                  ? `D/E ${leverageRisk.debt_to_equity?.toFixed(2) ?? "—"} · ISCR ${leverageRisk.interest_service_coverage?.toFixed(2) ?? "—"} · ${leverageRisk.score_impact ?? 0} points.`
                  : "The current filing is missing or ambiguous; leverage remains unknown."}
            </AlertDescription>
          </Alert>
        </div>
      </section>

      {/* 2. Trade Decision Checklist (Strengths, Risks, Review Focus) */}
      {(detail.ai_opinion.strengths.length > 0 || detail.ai_opinion.risks.length > 0 || detail.ai_opinion.review_focus.length > 0) && (
        <section className="grid gap-4 md:grid-cols-3">
          {detail.ai_opinion.strengths.length > 0 && (
            <div className="flex flex-col gap-2.5 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
                <TrendingUpIcon className="size-4" />
                <span>Key Strengths ({detail.ai_opinion.strengths.length})</span>
              </div>
              <div className="flex flex-col gap-2">
                {detail.ai_opinion.strengths.map((note, index) => (
                  <div className="rounded-lg border border-emerald-500/20 bg-background p-2.5 text-xs text-foreground shadow-2xs" key={`str-${index}`}>
                    <p className="font-medium">{note.text}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {detail.ai_opinion.risks.length > 0 && (
            <div className="flex flex-col gap-2.5 rounded-xl border border-red-500/20 bg-red-500/5 p-4">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-red-700 dark:text-red-400">
                <ShieldAlertIcon className="size-4" />
                <span>Risks & Headwinds ({detail.ai_opinion.risks.length})</span>
              </div>
              <div className="flex flex-col gap-2">
                {detail.ai_opinion.risks.map((note, index) => (
                  <div className="rounded-lg border border-red-500/20 bg-background p-2.5 text-xs text-foreground shadow-2xs" key={`risk-${index}`}>
                    <p className="font-medium">{note.text}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {detail.ai_opinion.review_focus.length > 0 && (
            <div className="flex flex-col gap-2.5 rounded-xl border border-blue-500/20 bg-blue-500/5 p-4">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-blue-700 dark:text-blue-400">
                <TargetIcon className="size-4" />
                <span>Review Focus ({detail.ai_opinion.review_focus.length})</span>
              </div>
              <div className="flex flex-col gap-2">
                {detail.ai_opinion.review_focus.map((note, index) => (
                  <div className="rounded-lg border border-blue-500/20 bg-background p-2.5 text-xs text-foreground shadow-2xs" key={`focus-${index}`}>
                    <p className="font-medium">{note.text}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* 3. Deterministic Python Score Breakdown */}
      {assessment && (
        <section className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-sm">
          <div className="flex flex-wrap items-end justify-between gap-4 border-b pb-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Deterministic Python Fit</p>
              <div className="mt-1 flex items-baseline gap-3">
                <strong className="text-3xl font-black tabular-nums">{scoreLabel(assessment)}</strong>
                <Badge className={cn(assessment.grade === "A" ? "bg-emerald-600 text-white" : assessment.grade === "B" ? "bg-blue-600 text-white" : "bg-muted text-muted-foreground")}>
                  Grade {assessment.grade}
                </Badge>
              </div>
            </div>
            <div className="text-right text-xs text-muted-foreground">
              <div>Data coverage: <span className="font-mono font-semibold text-foreground">{assessment.coverage_pct.toFixed(0)}%</span></div>
              <div>Available points: <span className="font-mono font-semibold text-foreground">{assessment.available_points.toFixed(0)} / {assessment.max_points.toFixed(0)}</span></div>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {assessment.components.map((component) => {
              const ratio = component.max_points > 0 ? Math.round((component.earned_points / component.max_points) * 100) : 0
              return (
                <div className="rounded-lg border bg-background p-3 shadow-2xs" key={component.name}>
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <strong className="font-semibold">{prettyKey(component.name)}</strong>
                    <span className="font-mono text-muted-foreground">{component.earned_points.toFixed(1)} / {component.available_points.toFixed(1)}</span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.min(100, Math.max(0, ratio))}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* 4. Company Overview & Key Metrics */}
      {facts?.company && (
        <section className="flex flex-col gap-3 rounded-xl border p-4 bg-card shadow-2xs">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Building2Icon className="size-4" />
            <span>Company Profile</span>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="rounded-lg bg-muted/30 p-2.5 border">
              <span className="text-muted-foreground block text-[11px]">Sector</span>
              <strong className="mt-0.5 block text-sm font-semibold">{facts.company.sector ?? "—"}</strong>
            </div>
            <div className="rounded-lg bg-muted/30 p-2.5 border">
              <span className="text-muted-foreground block text-[11px]">Industry</span>
              <strong className="mt-0.5 block text-sm font-semibold">{facts.company.industry ?? "—"}</strong>
            </div>
            <p className="col-span-2 text-xs leading-relaxed text-muted-foreground">
              {facts.company.description ?? "No company description supplied."}
            </p>
          </div>
        </section>
      )}

      {/* 5. Clean Metric Tables */}
      <HistoryTable histories={facts?.histories?.quarterly} title="Quarterly Sales & Profit Trend" />
      <HistoryTable histories={facts?.histories?.annual} title="Annual Financial Performance" />

      {holdings.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Shareholding Ownership Trend</h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {holdings.map(([category, points]) => (
              <div className="rounded-lg border bg-card p-3" key={category}>
                <strong className="text-xs font-semibold">{prettyKey(category)}</strong>
                <p className="mt-1.5 font-mono text-xs text-muted-foreground">
                  {points
                    .map(
                      (point) =>
                        `${point.period}: ${formatHistoryValue(point)}%`,
                    )
                    .join(" · ")}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 6. Collapsible Developer Audit Trace (No clutter in trade view) */}
      <Separator className="my-2" />
      <FundamentalPipelineTrace detail={detail} />
    </div>
  )
}

export function FundamentalsView() {
  const runsQuery = useScanRuns()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState<FitFilter>("all")
  const [sortMode, setSortMode] = useState<SortMode>("technical")
  const [refreshConfirmOpen, setRefreshConfirmOpen] = useState(false)

  const activeRun = runsQuery.data?.find((run) => run.id === selectedRunId)
  const resultsQuery = useScanResults(selectedRunId, activeRun?.status)
  const detailQuery = useFundamentalDetail(selectedResultId)
  const triggerPassMutation = useTriggerFundamentalPass()
  const progressQuery = useFundamentalPassProgress(selectedRunId)
  const controlsQuery = useFundamentalControls()
  const setControlMutation = useSetFundamentalControl()
  const [pendingControl, setPendingControl] = useState<"processing" | "ai" | null>(null)

  const isProcessing = useMemo(
    () =>
      resultsQuery.data?.some(
        (r) => r.fundamental_status === "queued" || r.fundamental_status === "running",
      ) ?? false,
    [resultsQuery.data],
  )

  useEffect(() => {
    if (selectedRunId || !runsQuery.data?.length) return
    setSelectedRunId(
      runsQuery.data.find((run) => run.status === "succeeded")?.id ??
        runsQuery.data[0].id,
    )
  }, [runsQuery.data, selectedRunId])

  const filtered = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase()
    const matching = (resultsQuery.data ?? []).filter((result) => {
      const matchesSearch =
        !normalizedSearch ||
        result.symbol.toLocaleLowerCase().includes(normalizedSearch) ||
        result.name?.toLocaleLowerCase().includes(normalizedSearch)
      return matchesSearch && matchesFilter(result, filter)
    })
    return [...matching].sort((left, right) => {
      if (sortMode === "technical") return left.rank - right.rank
      const leftScore = left.fundamental_assessment?.score
      const rightScore = right.fundamental_assessment?.score
      if (leftScore === null || leftScore === undefined) return 1
      if (rightScore === null || rightScore === undefined) return -1
      return rightScore - leftScore
    })
  }, [filter, resultsQuery.data, search, sortMode])

  const progressData = progressQuery.data
  const totalTokensUsed = progressData ? progressData.input_tokens + progressData.reasoning_tokens + progressData.output_tokens : 0
  const tokenBudget = progressData?.token_budget ?? 1500000

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      {/* 1. Header Bar */}
      <header className="flex flex-col gap-4 border-b bg-card px-6 py-4 xl:flex-row xl:items-center xl:justify-between shadow-2xs">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="rounded-lg bg-primary/10 p-2 text-primary">
              <BrainCircuitIcon className="size-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight">Fundamental Screener & AI Analyst</h1>
              <p className="text-xs text-muted-foreground">
                Python evaluates hard growth metrics; OpenRouter GPT-5.6 provides grounded trade decision second opinions.
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Field className="w-60">
            <FieldLabel className="sr-only" htmlFor="fundamental-search">
              Search symbol
            </FieldLabel>
            <div className="relative">
              <SearchIcon
                aria-hidden="true"
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                className="pl-8 text-xs"
                id="fundamental-search"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search symbol or company"
                value={search}
              />
            </div>
          </Field>

          <Field>
            <FieldLabel className="sr-only" htmlFor="fundamental-filter">Filter fit</FieldLabel>
            <NativeSelect className="text-xs" id="fundamental-filter" onChange={(e) => setFilter(e.target.value as FitFilter)} value={filter}>
              <NativeSelectOption value="all">All Fit Grades</NativeSelectOption>
              <NativeSelectOption value="A">Grade A Fit</NativeSelectOption>
              <NativeSelectOption value="B">Grade B Fit</NativeSelectOption>
              <NativeSelectOption value="C">Grade C Fit</NativeSelectOption>
              <NativeSelectOption value="D">Grade D Fit</NativeSelectOption>
              <NativeSelectOption value="insufficient">Insufficient Data</NativeSelectOption>
              <NativeSelectOption value="processing">Processing</NativeSelectOption>
              <NativeSelectOption value="unavailable">Failed / Unavailable</NativeSelectOption>
            </NativeSelect>
          </Field>

          <Field>
            <FieldLabel className="sr-only" htmlFor="fundamental-sort">Sort order</FieldLabel>
            <NativeSelect className="text-xs" id="fundamental-sort" onChange={(e) => setSortMode(e.target.value as SortMode)} value={sortMode}>
              <NativeSelectOption value="fundamental">Sort: Fundamental Fit</NativeSelectOption>
              <NativeSelectOption value="technical">Sort: Technical Rank</NativeSelectOption>
            </NativeSelect>
          </Field>

          <Field>
            <FieldLabel className="sr-only" htmlFor="fundamental-run">Scanner run</FieldLabel>
            <NativeSelect
              className="min-w-64 text-xs"
              disabled={!runsQuery.data?.length}
              id="fundamental-run"
              onChange={(e) => {
                setSelectedRunId(e.target.value)
                setSelectedResultId(null)
              }}
              value={selectedRunId ?? ""}
            >
              {!runsQuery.data?.length && <NativeSelectOption value="">No scanner runs</NativeSelectOption>}
              {(runsQuery.data ?? []).map((run) => (
                <NativeSelectOption key={run.id} value={run.id}>
                  {formatRun(run)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
        </div>
      </header>

      {/* 2. Unified Processing & Execution Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b bg-muted/30 px-6 py-3">
        <div className="flex items-center gap-4">
          <Button
            disabled={
              !selectedRunId ||
              activeRun?.status === "queued" ||
              activeRun?.status === "running" ||
              triggerPassMutation.isPending ||
              isProcessing ||
              controlsQuery.data?.processing.paused
            }
            onClick={() => {
              if (selectedRunId) triggerPassMutation.mutate({ runId: selectedRunId })
            }}
            size="sm"
            variant="default"
          >
            {triggerPassMutation.isPending || isProcessing ? (
              <Spinner className="mr-1.5 size-4" />
            ) : (
              <RotateCwIcon aria-hidden="true" className="mr-1.5 size-4" />
            )}
            {triggerPassMutation.isPending
              ? "Enqueuing..."
              : isProcessing
                ? "Processing..."
                : "Run Analysis"}
          </Button>

          <div className="flex items-center gap-2">
            <Button
              disabled={controlsQuery.isLoading || setControlMutation.isPending}
              onClick={() => setPendingControl("processing")}
              size="sm"
              variant={controlsQuery.data?.processing.paused ? "outline" : "secondary"}
            >
              {controlsQuery.data?.processing.paused ? <PlayCircleIcon className="mr-1 size-3.5 text-emerald-600" /> : <PauseCircleIcon className="mr-1 size-3.5 text-amber-600" />}
              {controlsQuery.data?.processing.paused ? "Resume Pipeline" : "Pause Pipeline"}
            </Button>

            <Button
              disabled={controlsQuery.isLoading || setControlMutation.isPending}
              onClick={() => setPendingControl("ai")}
              size="sm"
              variant={controlsQuery.data?.ai.paused ? "outline" : "ghost"}
            >
              {controlsQuery.data?.ai.paused ? <PlayCircleIcon className="mr-1 size-3.5 text-emerald-600" /> : <PauseCircleIcon className="mr-1 size-3.5 text-muted-foreground" />}
              {controlsQuery.data?.ai.paused ? "Resume AI" : "Pause AI"}
            </Button>

            <Button
              disabled={
                !selectedRunId ||
                triggerPassMutation.isPending ||
                isProcessing ||
                controlsQuery.data?.processing.paused
              }
              onClick={() => setRefreshConfirmOpen(true)}
              size="sm"
              title="Refresh Upstox source data"
              variant="outline"
            >
              <RotateCwIcon aria-hidden="true" className="mr-1 size-3.5" />
              Refresh Source
            </Button>
          </div>
        </div>

        {/* Live Token & Execution Status Pill */}
        {progressData && (
          <div className="flex items-center gap-3 rounded-lg border bg-card px-3.5 py-1.5 text-xs shadow-2xs">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-emerald-500 animate-pulse" />
              <strong className="font-semibold">{progressData.status}</strong>
              {progressData.current_symbol && (
                <span className="font-mono text-muted-foreground">· #{progressData.current_rank} {progressData.current_symbol}</span>
              )}
            </div>
            <Separator className="h-4" orientation="vertical" />
            <div className="flex items-center gap-3 font-mono text-[11px] text-muted-foreground">
              <span><strong className="text-foreground">{progressData.counts.succeeded ?? 0}</strong> complete</span>
              <span>Tokens: <strong className="text-foreground">{totalTokensUsed.toLocaleString()}</strong> / {(tokenBudget / 1000).toFixed(0)}k</span>
            </div>
          </div>
        )}
      </div>

      {/* 3. Results Table */}
      <div className="min-h-0 flex-1 overflow-auto">
        {resultsQuery.isLoading ? (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Spinner /> Loading fundamental shortlist…
          </div>
        ) : resultsQuery.isError ? (
          <div className="flex h-full items-center justify-center p-6">
            <Alert className="max-w-xl" variant="destructive">
              <XCircleIcon aria-hidden="true" />
              <AlertTitle>Could not load fundamentals</AlertTitle>
              <AlertDescription>
                {resultsQuery.error instanceof Error
                  ? resultsQuery.error.message
                  : "The screening API is unavailable."}
              </AlertDescription>
            </Alert>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full items-center justify-center p-6">
            <Alert className="max-w-xl">
              <AlertTriangleIcon aria-hidden="true" />
              <AlertTitle>No matching fundamental results</AlertTitle>
              <AlertDescription>
                Change the run, search, or fit filter to inspect another setup.
              </AlertDescription>
            </Alert>
          </div>
        ) : (
          <table className="w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 border-b bg-card text-xs text-muted-foreground shadow-2xs">
              <tr>
                <th className="px-6 py-3">Stock & Company</th>
                <th className="px-4 py-3">Python Fit Score</th>
                <th className="px-4 py-3">AI Verdict</th>
                <th className="px-4 py-3">Data Coverage</th>
                <th className="px-4 py-3">Strongest Factor</th>
                <th className="px-4 py-3">Review Flags</th>
                <th className="px-6 py-3 text-right">Tech Rank</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((result) => {
                const concerns = keyRedFlags(result)
                const assessment = result.fundamental_assessment
                const aiVerdict = result.llm_verdict ?? (result.ai_status === "succeeded" ? "pass" : null)

                return (
                  <tr
                    className={cn(
                      "cursor-pointer transition-colors hover:bg-muted/50",
                      selectedResultId === result.id && "bg-muted/80 font-medium",
                    )}
                    key={result.id}
                    onClick={() => setSelectedResultId(result.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault()
                        setSelectedResultId(result.id)
                      }
                    }}
                    tabIndex={0}
                  >
                    <td className="px-6 py-3.5">
                      <div className="flex items-center gap-2">
                        <strong className="text-base font-bold tracking-tight text-foreground">{result.symbol}</strong>
                      </div>
                      <span className="block max-w-72 truncate text-xs text-muted-foreground">
                        {result.name ?? result.fyers_symbol}
                      </span>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-2">
                        {fitBadge(result)}
                        <span className="font-mono text-xs font-bold text-foreground">{scoreLabel(assessment)}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      {aiVerdictBadge(aiVerdict, result.ai_status)}
                    </td>
                    <td className="px-4 py-3.5 font-mono text-xs text-muted-foreground">
                      {assessment ? `${assessment.coverage_pct.toFixed(0)}%` : "—"}
                    </td>
                    <td className="px-4 py-3.5 text-xs font-medium">{strongestFactor(assessment)}</td>
                    <td className="max-w-xs px-4 py-3.5">
                      {concerns.length ? (
                        <div className="flex flex-wrap gap-1">
                          {concerns.map((flag) => (
                            <Badge key={flag} variant="destructive">
                              {prettyKey(flag)}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">Clean · No flags</span>
                      )}
                    </td>
                    <td className="px-6 py-3.5 text-right font-mono font-bold text-muted-foreground">
                      #{result.rank}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* 4. Stock Decision Inspector Sheet */}
      <Sheet
        onOpenChange={(open) => {
          if (!open) setSelectedResultId(null)
        }}
        open={Boolean(selectedResultId)}
      >
        <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-3xl border-l p-0">
          <SheetHeader className="sticky top-0 z-10 border-b bg-card px-6 py-4 pr-12 shadow-2xs">
            <div className="flex items-center justify-between">
              <div>
                <SheetTitle className="text-xl font-black tracking-tight">
                  {detailQuery.data?.instrument.symbol ?? "Stock Fundamental Assessment"}
                </SheetTitle>
                <SheetDescription className="text-xs text-muted-foreground">
                  {detailQuery.data?.instrument.name ?? "Loading stock facts & AI trade verdict…"}
                </SheetDescription>
              </div>
            </div>
          </SheetHeader>

          {detailQuery.isLoading ? (
            <div className="flex flex-1 items-center justify-center gap-2 py-20 text-muted-foreground">
              <Spinner /> Loading stock assessment…
            </div>
          ) : detailQuery.isError ? (
            <div className="p-6">
              <Alert variant="destructive">
                <XCircleIcon aria-hidden="true" />
                <AlertTitle>Could not load assessment</AlertTitle>
                <AlertDescription>
                  {detailQuery.error instanceof Error
                    ? detailQuery.error.message
                    : "The fundamentals detail endpoint is unavailable."}
                </AlertDescription>
              </Alert>
            </div>
          ) : detailQuery.data ? (
            <FundamentalInspector detail={detailQuery.data} />
          ) : null}
        </SheetContent>
      </Sheet>

      {/* Refresh Confirmation Modal */}
      <AlertDialog open={refreshConfirmOpen} onOpenChange={setRefreshConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Refresh Upstox Source Data?</AlertDialogTitle>
            <AlertDialogDescription>
              This refetches technical survivors from Upstox, invalidates old snapshots, and computes fresh AI analysis.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={triggerPassMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={triggerPassMutation.isPending || !selectedRunId}
              onClick={() => {
                if (!selectedRunId) return
                triggerPassMutation.mutate(
                  { runId: selectedRunId, mode: "refresh_stale" },
                  { onSuccess: () => setRefreshConfirmOpen(false) },
                )
              }}
            >
              {triggerPassMutation.isPending ? <Spinner data-icon="inline-start" /> : null}
              Refresh Source
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Control Pause/Resume Confirmation Modal */}
      <AlertDialog open={pendingControl !== null} onOpenChange={(open) => !open && setPendingControl(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingControl === "processing"
                ? controlsQuery.data?.processing.paused ? "Resume fundamental processing?" : "Pause fundamental processing?"
                : controlsQuery.data?.ai.paused ? "Resume AI annotations?" : "Pause AI annotations?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingControl === "processing"
                ? "The active P7 pipeline will pause at the next stock boundary."
                : "No new OpenRouter calls will be issued; deterministic Upstox scoring will continue."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={setControlMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={setControlMutation.isPending || pendingControl === null}
              onClick={() => {
                if (!pendingControl) return
                const paused = pendingControl === "processing" ? !controlsQuery.data?.processing.paused : !controlsQuery.data?.ai.paused
                setControlMutation.mutate(
                  { control: pendingControl, paused, reason: `Human ${paused ? "paused" : "resumed"} ${pendingControl === "processing" ? "fundamental processing" : "AI annotations"} from workspace.` },
                  { onSuccess: () => setPendingControl(null) },
                )
              }}
            >
              {setControlMutation.isPending ? <Spinner data-icon="inline-start" /> : null}
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
