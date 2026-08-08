import { useEffect, useMemo, useState } from "react"
import {
  AlertTriangleIcon,
  BrainCircuitIcon,
  Building2Icon,
  DatabaseZapIcon,
  RotateCwIcon,
  SearchIcon,
  PauseCircleIcon,
  PlayCircleIcon,
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
  return `${date} · ${run.passing_count} ranked setups`
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
  if (grade === "A") return <Badge>Fit A</Badge>
  if (grade === "B") return <Badge variant="secondary">Fit B</Badge>
  if (grade === "C") return <Badge variant="outline">Fit C</Badge>
  if (grade === "D") return <Badge variant="destructive">Fit D</Badge>
  return <Badge variant="outline">Awaiting analysis</Badge>
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

function formatMetric(value: unknown, unit?: string | null): string {
  if (typeof value === "number") {
    if (unit === "percent" || unit === "percentage_points") {
      return `${value.toFixed(2)}%`
    }
    return value.toLocaleString("en-IN", { maximumFractionDigits: 2 })
  }
  if (typeof value === "string") return value
  if (value === null || value === undefined) return "—"
  if (Array.isArray(value)) return `${value.length} records`
  if (typeof value === "object") {
    const record = value as Record<string, unknown>
    const numeric =
      record.value_pct ?? record.change_percentage_points ?? record.value
    const period = typeof record.period === "string" ? record.period : null
    if (typeof numeric === "number") {
      return `${numeric.toFixed(2)}${unit === "ratio" ? "×" : "%"}${period ? ` · ${period}` : ""}`
    }
  }
  return "Available"
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
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Metric</th>
              <th className="px-3 py-2">History</th>
            </tr>
          </thead>
          <tbody className="divide-y">
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
      <section className="rounded-lg border border-dashed p-4">
        <h3 className="text-sm font-semibold">Pipeline audit trace</h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          Load the stored Upstox payload, normalization, Python contract, and sanitized model attempts only when needed.
        </p>
        <Button className="mt-3" onClick={() => setOpened(true)} size="sm" variant="outline">
          Open audit trace
        </Button>
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-3 rounded-lg border p-4">
      <div>
        <h3 className="text-sm font-semibold">Pipeline audit trace</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Upstox → Normalized → Python fit → AI request → AI response
        </p>
      </div>
      {traceQuery.isLoading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Spinner className="size-4" /> Loading stored trace…
        </div>
      ) : traceQuery.isError ? (
        <Alert variant="destructive">
          <XCircleIcon aria-hidden="true" />
          <AlertTitle>Could not load trace</AlertTitle>
          <AlertDescription>
            {traceQuery.error instanceof Error ? traceQuery.error.message : "The trace endpoint is unavailable."}
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
                  className="ml-1"
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
          <pre className="max-h-96 overflow-auto rounded-lg border bg-muted/30 p-3 text-[11px] whitespace-pre-wrap">
            {JSON.stringify(selectedPayload, null, 2)}
          </pre>
        </>
      )}
    </section>
  )
}

function FundamentalInspector({ detail }: { detail: FundamentalDetail }) {
  const facts = detail.snapshot?.normalized_facts
  const evidence = facts?.evidence ?? {}
  const ratios = Object.entries(facts?.ratios ?? {})
  const holdings = Object.entries(facts?.histories?.shareholding ?? {})
  const corporateActions = evidence["corporate_actions.recent"]?.value
  const assessment = detail.fundamental.assessment
  const criteria = Array.isArray(detail.fundamental.scorecard.criteria)
    ? detail.fundamental.scorecard.criteria as Array<{
        name: string
        status: "positive" | "negative" | "mixed" | "unknown" | "not_applicable"
        explanation: string
        evidence_keys: string[]
      }>
    : []

  return (
    <div className="flex flex-col gap-6 px-4 pb-8">
      {detail.fundamental.status === "failed" && (
        <Alert variant="destructive">
          <DatabaseZapIcon aria-hidden="true" />
          <AlertTitle>Fundamental data unavailable</AlertTitle>
          <AlertDescription>
            Upstox data or deterministic normalization could not be completed. Open the audit trace for the stored error.
          </AlertDescription>
        </Alert>
      )}

      {detail.ai_opinion.status === "failed" && (
        <Alert>
          <BrainCircuitIcon aria-hidden="true" />
          <AlertTitle>AI second opinion unavailable</AlertTitle>
          <AlertDescription>
            The Python fit remains authoritative. Open the audit trace for model-attempt details.
          </AlertDescription>
        </Alert>
      )}

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {fitBadge({
            fundamental_status: detail.fundamental.status,
            fundamental_assessment: assessment,
          })}
          <Badge variant="outline">
            AI: {detail.ai_opinion.verdict ?? detail.ai_opinion.status.replaceAll("_", " ")}
          </Badge>
          {detail.snapshot && (
            <>
              <Badge variant="outline">{detail.snapshot.provider}</Badge>
              <Badge variant="outline">
                {detail.snapshot.statement_type}
              </Badge>
            </>
          )}
        </div>
        <p className="leading-6 text-muted-foreground">
          {detail.ai_opinion.summary ??
            "Minervini-inspired fundamental fit is available. The independent AI second opinion is not available."}
        </p>
      </section>

      {assessment && (
        <section className="flex flex-col gap-4 rounded-lg border bg-muted/20 p-4">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Fundamental fit</p>
              <div className="mt-1 flex items-baseline gap-3">
                <strong className="text-3xl tabular-nums">{scoreLabel(assessment)}</strong>
                <Badge variant={assessment.grade === "D" ? "destructive" : assessment.grade === "insufficient" ? "outline" : assessment.grade === "A" ? "default" : "secondary"}>
                  Grade {assessment.grade}
                </Badge>
              </div>
            </div>
            <div className="text-right text-xs text-muted-foreground">
              <div>{assessment.coverage_pct.toFixed(0)}% data coverage</div>
              <div>{assessment.available_points.toFixed(0)} / {assessment.max_points.toFixed(0)} points available</div>
            </div>
          </div>
          {assessment.grade === "insufficient" && (
            <Alert>
              <DatabaseZapIcon aria-hidden="true" />
              <AlertTitle>Insufficient financial history</AlertTitle>
              <AlertDescription>{assessment.insufficient_reason ?? "More supported history is required before assigning a score."}</AlertDescription>
            </Alert>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            {assessment.components.map((component) => {
              const ratio = component.max_points > 0 ? Math.round((component.earned_points / component.max_points) * 100) : 0
              return (
                <div className="rounded-md border bg-background p-3" key={component.name}>
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <strong>{prettyKey(component.name)}</strong>
                    <span className="font-mono text-muted-foreground">{component.earned_points.toFixed(1)} / {component.available_points.toFixed(1)} available</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted" aria-label={`${prettyKey(component.name)} score ${ratio}%`}>
                    <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.min(100, Math.max(0, ratio))}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {(detail.ai_opinion.strengths.length > 0 || detail.ai_opinion.risks.length > 0 || detail.ai_opinion.review_focus.length > 0) && (
        <section className="grid gap-4 md:grid-cols-3">
          {([
            ["Strengths", detail.ai_opinion.strengths, "default"],
            ["Risks", detail.ai_opinion.risks, "destructive"],
            ["Review focus", detail.ai_opinion.review_focus, "outline"],
          ] as const).map(([title, notes, variant]) => notes.length > 0 && (
            <div className="flex flex-col gap-2 rounded-lg border p-3" key={title}>
              <h3 className="text-sm font-semibold">{title}</h3>
              {notes.map((note, index) => (
                <div className="flex flex-col gap-1" key={`${title}-${index}`}>
                  <Badge className="w-fit max-w-full whitespace-normal text-left" variant={variant}>{note.text}</Badge>
                  {note.reference_ids.length > 0 && <span className="text-[11px] text-muted-foreground">References: {note.reference_ids.join(", ")}</span>}
                </div>
              ))}
            </div>
          ))}
        </section>
      )}

      {assessment && assessment.provider_limitations.length > 0 && (
        <Alert>
          <DatabaseZapIcon aria-hidden="true" />
          <AlertTitle>Coverage limits are neutral</AlertTitle>
          <AlertDescription>
            The fit score excludes unsupported fields such as quarterly EPS YoY. These gaps reduce coverage; they are not negative signals.
          </AlertDescription>
        </Alert>
      )}

      {facts?.company && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Building2Icon aria-hidden="true" />
            <h3 className="text-sm font-semibold">Company profile</h3>
          </div>
          <div className="grid grid-cols-2 gap-3 rounded-lg border p-3 text-xs">
            <div>
              <span className="text-muted-foreground">Sector</span>
              <strong className="mt-1 block">
                {facts.company.sector ?? "—"}
              </strong>
            </div>
            <div>
              <span className="text-muted-foreground">Industry</span>
              <strong className="mt-1 block">
                {facts.company.industry ?? "—"}
              </strong>
            </div>
            <p className="col-span-2 leading-5 text-muted-foreground">
              {facts.company.description ?? "No company description supplied."}
            </p>
          </div>
        </section>
      )}

      {criteria.length > 0 && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <BrainCircuitIcon aria-hidden="true" />
            <h3 className="text-sm font-semibold">Scoring evidence</h3>
          </div>
          <div className="flex flex-col gap-2">
            {criteria.map((criterion) => (
              <article className="rounded-lg border p-3" key={criterion.name}>
                <div className="flex items-center justify-between gap-3">
                  <strong>{prettyKey(criterion.name)}</strong>
                  <Badge
                    variant={
                      criterion.status === "negative"
                        ? "destructive"
                        : criterion.status === "positive"
                          ? "default"
                          : "secondary"
                    }
                  >
                    {criterion.status}
                  </Badge>
                </div>
                <p className="mt-2 leading-5 text-muted-foreground">
                  {criterion.explanation}
                </p>
                {criterion.evidence_keys.length > 0 && (
                  <dl className="mt-3 flex flex-col gap-2">
                    {criterion.evidence_keys.map((key) => (
                      <div
                        className="flex items-start justify-between gap-4 rounded bg-muted/40 px-2 py-1.5"
                        key={key}
                      >
                        <dt className="text-xs text-muted-foreground">
                          {evidence[key]?.label ?? key}
                        </dt>
                        <dd className="shrink-0 font-mono text-xs font-semibold">
                          {formatMetric(
                            evidence[key]?.value,
                            evidence[key]?.unit,
                          )}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      <HistoryTable
        histories={facts?.histories?.quarterly}
        title="Quarterly financial history"
      />
      <HistoryTable
        histories={facts?.histories?.annual}
        title="Annual financial history"
      />

      {ratios.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Ratios and sector comparison</h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {ratios.map(([name, values]) => (
              <div className="rounded-lg border p-3" key={name}>
                <strong className="text-xs">{prettyKey(name)}</strong>
                <div className="mt-2 flex justify-between font-mono text-xs">
                  <span>Company {formatMetric(values.company)}</span>
                  <span className="text-muted-foreground">
                    Sector {formatMetric(values.sector)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {holdings.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Shareholding history</h3>
          {holdings.map(([category, points]) => (
            <div className="rounded-lg border p-3" key={category}>
              <strong className="text-xs">{prettyKey(category)}</strong>
              <p className="mt-2 font-mono text-xs text-muted-foreground">
                {points
                  .map(
                    (point) =>
                      `${point.period}: ${formatHistoryValue(point)}%`,
                  )
                  .join(" · ")}
              </p>
            </div>
          ))}
        </section>
      )}

      {Array.isArray(corporateActions) && corporateActions.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Recent corporate actions</h3>
          <pre className="overflow-x-auto rounded-lg border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
            {JSON.stringify(corporateActions, null, 2)}
          </pre>
        </section>
      )}

      {facts?.provider_sections && (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Complete Upstox response sections</h3>
          <pre className="overflow-x-auto rounded-lg border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
            {JSON.stringify(facts.provider_sections, null, 2)}
          </pre>
        </section>
      )}

      {((assessment?.red_flags.length ?? 0) > 0 ||
        detail.fundamental.missing_data.length > 0) && (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Review warnings</h3>
          <div className="flex flex-wrap gap-2">
            {(assessment?.red_flags ?? []).map((flag) => (
              <Badge key={flag} variant="destructive">
                {prettyKey(flag)}
              </Badge>
            ))}
            {detail.fundamental.missing_data.map((field) => (
              <Badge key={field} variant="outline">
                Missing: {prettyKey(field)}
              </Badge>
            ))}
          </div>
        </section>
      )}

      {detail.fundamental.provider_limitations.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Provider limitations</h3>
          <div className="flex flex-wrap gap-2">
            {detail.fundamental.provider_limitations.map((field) => (
              <Badge key={field} variant="outline">
                Upstox does not provide: {prettyKey(field)}
              </Badge>
            ))}
          </div>
        </section>
      )}

      <FundamentalPipelineTrace detail={detail} />

      <Separator />
      <section className="grid grid-cols-2 gap-3 text-xs text-muted-foreground">
        <span>Snapshot ID: {detail.snapshot?.id ?? "Not available"}</span>
        <span>
          Periods: Q {detail.snapshot?.latest_quarterly_period ?? "—"} · A {detail.snapshot?.latest_annual_period ?? "—"}
        </span>
        <span>
          Snapshot: {detail.snapshot?.fetched_at
            ? new Intl.DateTimeFormat("en-IN", {
                dateStyle: "medium",
                timeStyle: "short",
              }).format(new Date(detail.snapshot.fetched_at))
            : "Not available"}
        </span>
        {detail.ai_opinion.model && (
          <>
            <span>
              Model: {String(detail.ai_opinion.model.provider ?? "unknown")} · {String(detail.ai_opinion.model.name ?? "unknown")}
            </span>
            <span>
              Prompt: {String(detail.ai_opinion.model.prompt_version ?? "—")} · Request {String(detail.ai_opinion.model.request_id ?? "—")}
            </span>
          </>
        )}
        <span>
          AI checked: {detail.ai_opinion.checked_at
            ? new Intl.DateTimeFormat("en-IN", {
                dateStyle: "medium",
                timeStyle: "short",
              }).format(new Date(detail.ai_opinion.checked_at))
            : "Not available"}
        </span>
      </section>
    </div>
  )
}

export function FundamentalsView() {
  const runsQuery = useScanRuns()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState<FitFilter>("all")
  const [sortMode, setSortMode] = useState<SortMode>("fundamental")
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

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex flex-col gap-4 border-b bg-card px-5 py-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BrainCircuitIcon aria-hidden="true" />
            <h1 className="text-lg font-semibold">Fundamental analysis</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Python grades the available evidence; AI provides a separate, grounded second opinion.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Field className="w-64">
            <FieldLabel className="sr-only" htmlFor="fundamental-search">
              Search fundamentals
            </FieldLabel>
            <div className="relative">
              <SearchIcon
                aria-hidden="true"
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                className="pl-8"
                id="fundamental-search"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search symbol or company"
                value={search}
              />
            </div>
          </Field>
          <Field>
            <FieldLabel className="sr-only" htmlFor="fundamental-filter">
              Filter verdicts
            </FieldLabel>
            <NativeSelect
              id="fundamental-filter"
              onChange={(event) =>
                setFilter(event.target.value as FitFilter)
              }
              value={filter}
            >
              <NativeSelectOption value="all">All fit grades</NativeSelectOption>
              <NativeSelectOption value="A">Grade A</NativeSelectOption>
              <NativeSelectOption value="B">Grade B</NativeSelectOption>
              <NativeSelectOption value="C">Grade C</NativeSelectOption>
              <NativeSelectOption value="D">Grade D</NativeSelectOption>
              <NativeSelectOption value="insufficient">Insufficient data</NativeSelectOption>
              <NativeSelectOption value="processing">
                Processing
              </NativeSelectOption>
              <NativeSelectOption value="unavailable">
                Failed / unavailable
              </NativeSelectOption>
            </NativeSelect>
          </Field>
          <Field>
            <FieldLabel className="sr-only" htmlFor="fundamental-sort">Sort fundamentals</FieldLabel>
            <NativeSelect id="fundamental-sort" onChange={(event) => setSortMode(event.target.value as SortMode)} value={sortMode}>
              <NativeSelectOption value="fundamental">Sort: fundamental fit</NativeSelectOption>
              <NativeSelectOption value="technical">Sort: technical rank</NativeSelectOption>
            </NativeSelect>
          </Field>
          <Field>
            <FieldLabel className="sr-only" htmlFor="fundamental-run">
              Scanner run
            </FieldLabel>
            <NativeSelect
              className="min-w-72"
              disabled={!runsQuery.data?.length}
              id="fundamental-run"
              onChange={(event) => {
                setSelectedRunId(event.target.value)
                setSelectedResultId(null)
              }}
              value={selectedRunId ?? ""}
            >
              {!runsQuery.data?.length && (
                <NativeSelectOption value="">No scanner runs</NativeSelectOption>
              )}
              {(runsQuery.data ?? []).map((run) => (
                <NativeSelectOption key={run.id} value={run.id}>
                  {formatRun(run)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
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
              if (selectedRunId) {
                triggerPassMutation.mutate({ runId: selectedRunId })
              }
            }}
            variant="outline"
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
                : "Run missing analysis"}
          </Button>
          <Button
            disabled={
              !selectedRunId ||
              triggerPassMutation.isPending ||
              isProcessing ||
              controlsQuery.data?.processing.paused
            }
            onClick={() => setRefreshConfirmOpen(true)}
            size="icon"
            title="Refresh Upstox source data"
            variant="ghost"
          >
            <RotateCwIcon aria-hidden="true" />
          </Button>
        </div>
      </header>

      <div className="flex flex-col gap-3 border-b bg-card px-5 py-3 lg:flex-row">
        <Alert className="flex-1">
          <DatabaseZapIcon aria-hidden="true" />
          <AlertTitle>
            Fundamental processing {controlsQuery.data?.processing.paused ? "paused" : "enabled"}
          </AlertTitle>
          <AlertDescription>
            Stops new Upstox and AI work for this pipeline. This does not affect trading automation.
          </AlertDescription>
          <div className="mt-3">
            <Button
              disabled={controlsQuery.isLoading || setControlMutation.isPending}
              onClick={() => setPendingControl("processing")}
              size="sm"
              type="button"
              variant={controlsQuery.data?.processing.paused ? "outline" : "destructive"}
            >
              {controlsQuery.data?.processing.paused ? <PlayCircleIcon data-icon="inline-start" /> : <PauseCircleIcon data-icon="inline-start" />}
              {controlsQuery.data?.processing.paused ? "Resume processing" : "Pause processing"}
            </Button>
          </div>
        </Alert>
        <Alert className="flex-1">
          <BrainCircuitIcon aria-hidden="true" />
          <AlertTitle>AI annotations {controlsQuery.data?.ai.paused ? "paused" : "enabled"}</AlertTitle>
          <AlertDescription>
            Stops only OpenRouter calls. Upstox facts and deterministic rules continue.
          </AlertDescription>
          <div className="mt-3">
            <Button
              disabled={controlsQuery.isLoading || setControlMutation.isPending}
              onClick={() => setPendingControl("ai")}
              size="sm"
              type="button"
              variant={controlsQuery.data?.ai.paused ? "outline" : "secondary"}
            >
              {controlsQuery.data?.ai.paused ? <PlayCircleIcon data-icon="inline-start" /> : <PauseCircleIcon data-icon="inline-start" />}
              {controlsQuery.data?.ai.paused ? "Resume AI" : "Pause AI"}
            </Button>
          </div>
        </Alert>
        {progressQuery.data && (
          <Alert className="flex-1">
            <DatabaseZapIcon aria-hidden="true" />
            <AlertTitle>
              {progressQuery.data.status} {progressQuery.data.current_symbol ? `· #${progressQuery.data.current_rank} ${progressQuery.data.current_symbol}` : ""}
            </AlertTitle>
            <AlertDescription>
              {progressQuery.data.counts.succeeded ?? 0} complete · {progressQuery.data.counts.rules_only ?? 0} rules-only · {progressQuery.data.input_tokens + progressQuery.data.reasoning_tokens + progressQuery.data.output_tokens}/{progressQuery.data.token_budget} tokens
            </AlertDescription>
          </Alert>
        )}
      </div>

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
                Change the run, search, or fit filter to inspect another
                ranked setup.
              </AlertDescription>
            </Alert>
          </div>
        ) : (
          <table className="w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 border-b bg-card text-xs text-muted-foreground">
              <tr>
                <th className="px-5 py-3">Company</th>
                <th className="px-4 py-3">Fundamental fit</th>
                <th className="px-4 py-3">Coverage</th>
                <th className="px-4 py-3">Strongest factor</th>
                <th className="px-4 py-3">AI status</th>
                <th className="px-4 py-3">Red flags</th>
                <th className="px-4 py-3">Latest periods</th>
                <th className="px-5 py-3 text-right">Rank</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((result) => {
                const concerns = keyRedFlags(result)
                const assessment = result.fundamental_assessment
                return (
                  <tr
                    className={cn(
                      "cursor-pointer transition-colors hover:bg-muted/50",
                      selectedResultId === result.id && "bg-muted",
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
                    <td className="px-5 py-3">
                      <strong className="block">{result.symbol}</strong>
                      <span className="block max-w-80 truncate text-xs text-muted-foreground">
                        {result.name ?? result.fyers_symbol}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {fitBadge(result)}
                        <span className="font-mono text-xs text-muted-foreground">{scoreLabel(assessment)}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      {assessment ? `${assessment.coverage_pct.toFixed(0)}%` : "—"}
                    </td>
                    <td className="px-4 py-3 text-xs">{strongestFactor(assessment)}</td>
                    <td className="px-4 py-3">
                      <Badge variant="outline">{result.ai_status.replaceAll("_", " ")}</Badge>
                      {result.llm_flags.ai_skip_reason && <span className="mt-1 block text-[11px] text-muted-foreground">{result.llm_flags.ai_skip_reason.replaceAll("_", " ")}</span>}
                    </td>
                    <td className="max-w-xl px-4 py-3">
                      {concerns.length ? (
                        <div className="flex flex-wrap gap-1.5">
                          {concerns.map((flag) => (
                            <Badge key={flag} variant="destructive">
                              {prettyKey(flag)}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {assessment?.grade === "insufficient" ? "More history needed" : result.llm_flags.summary ?? "No red flags"}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      Q {result.fundamentals_provenance?.latest_quarterly_period ?? "—"}
                      <br />A {result.fundamentals_provenance?.latest_annual_period ?? "—"}
                    </td>
                    <td className="px-5 py-3 text-right font-mono">
                      #{result.rank}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <Sheet
        onOpenChange={(open) => {
          if (!open) setSelectedResultId(null)
        }}
        open={Boolean(selectedResultId)}
      >
        <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-3xl">
          <SheetHeader className="sticky top-0 border-b bg-popover pr-12">
            <SheetTitle>
              {detailQuery.data?.instrument.symbol ?? "Fundamental analysis"}
            </SheetTitle>
            <SheetDescription>
              {detailQuery.data?.instrument.name ??
                "Loading normalized facts and AI evidence…"}
            </SheetDescription>
          </SheetHeader>
          {detailQuery.isLoading ? (
            <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground">
              <Spinner /> Loading analysis…
            </div>
          ) : detailQuery.isError ? (
            <div className="p-4">
              <Alert variant="destructive">
                <XCircleIcon aria-hidden="true" />
                <AlertTitle>Could not load analysis</AlertTitle>
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

      <AlertDialog open={refreshConfirmOpen} onOpenChange={setRefreshConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Refresh Upstox source data?</AlertDialogTitle>
            <AlertDialogDescription>
              This refetches the selected technical survivors, invalidates old source snapshots, and may use OpenRouter credits for eligible results. Deterministic fit remains read-only.
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
              Refresh source data
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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
                ? "The active P7 run will stop at the next safe boundary. An upstream request already accepted may still finish."
                : "No new OpenRouter calls will be made after the control is observed; deterministic Upstox scoring remains available."}
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
                  { control: pendingControl, paused, reason: `Human ${paused ? "paused" : "resumed"} ${pendingControl === "processing" ? "fundamental processing" : "AI annotations"} from the fundamentals workspace.` },
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
