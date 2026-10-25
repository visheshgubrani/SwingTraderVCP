import { useNavigate, useParams } from "react-router"
import { ArrowLeftIcon, LineChartIcon, ShieldAlertIcon } from "lucide-react"

import { EmptyState, Panel, SectionLabel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { API_BASE_URL } from "@/lib/api"
import { fmtDate, fmtDateTime, fmtNum } from "@/lib/format"
import { shortSymbol } from "@/lib/marketSymbols"
import { useProposalAttempt, type ProposalGenerationAttempt } from "./api"
import { AttemptOutcomeChip, attemptOutcome, humanizeCode, KV, ProposalCharts } from "./proposalUi"

function detailValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—"
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : fmtNum(value)
  if (typeof value === "boolean") return value ? "yes" : "no"
  if (Array.isArray(value)) return value.map(detailValue).join(", ")
  if (typeof value === "object") return JSON.stringify(value)
  return String(value).replaceAll("_", " ")
}

function ReasonPanel({ attempt, onOpenProposal }: { attempt: ProposalGenerationAttempt; onOpenProposal?: (id: string) => void }) {
  const outcome = attemptOutcome(attempt)
  const details = Object.entries(attempt.error_details ?? {}).filter(([key]) => key !== "existing_proposal_id")
  const existingId = attempt.error_details?.existing_proposal_id
  if (attempt.status === "valid" && !attempt.error_type) return null
  return (
    <Panel
      className={outcome.tone === "rej" ? "border-ko/40" : undefined}
      description={attempt.error_message ?? undefined}
      title={`Why: ${humanizeCode(attempt.error_type) === "—" ? outcome.label : humanizeCode(attempt.error_type)}`}
    >
      {details.length > 0 ? (
        <div className="grid gap-x-6 md:grid-cols-2">
          {details.map(([key, value]) => (
            <KV key={key} label={humanizeCode(key)}>
              {detailValue(value)}
            </KV>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-text">No further detail was recorded.</p>
      )}
      {typeof existingId === "string" && onOpenProposal && (
        <button className="link-act mt-2 !px-0" onClick={() => onOpenProposal(existingId)} type="button">
          Open the existing proposal →
        </button>
      )}
    </Panel>
  )
}

/** One generation attempt: outcome, reasons, Gemini evidence, charts, audit. */
export function AttemptDetail({
  attemptId,
  onOpenProposal,
  onOpenRun,
}: {
  attemptId: string
  onOpenProposal?: (id: string) => void
  onOpenRun?: (runId: string) => void
}) {
  const { openChart } = useTradingAppContext()
  const { data: attempt, isLoading, error } = useProposalAttempt(attemptId)

  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted-text">
        <Spinner /> Loading attempt…
      </div>
    )
  }
  if (error || !attempt) {
    return (
      <EmptyState icon={ShieldAlertIcon} title="Attempt not found" tone="bad">
        {error instanceof Error ? error.message : "The requested generation attempt could not be loaded."}
      </EmptyState>
    )
  }

  const structured = (attempt.structured_output ?? {}) as Record<string, unknown>
  const baseQuality = (structured.base_quality ?? {}) as Record<string, unknown>
  const redFlags = Array.isArray(structured.red_flags) ? (structured.red_flags as string[]) : []
  const assessments = Array.isArray(structured.candidate_assessments)
    ? (structured.candidate_assessments as Array<{ index?: number; action?: string; merge_with_index?: number | null; note?: string }>)
    : []
  const durationSeconds = attempt.completed_at
    ? (new Date(attempt.completed_at).getTime() - new Date(attempt.started_at).getTime()) / 1000
    : null
  const chips: [string, unknown][] = [
    ["Classification", structured.classification ?? structured.verdict],
    ["Forming state", structured.forming_state],
    ["Primary reason", structured.primary_reason],
    ["Tightening", structured.progressive_tightening],
    ["Volume dry-up", structured.volume_dry_up],
    ["Stage 2", baseQuality.stage2_context],
    ["Confidence (display only)", structured.confidence],
  ]

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-bold tracking-tight">{shortSymbol(attempt.symbol)}</h2>
            <AttemptOutcomeChip attempt={attempt} />
            <span className="font-mono text-2xs text-muted-text">attempt #{attempt.attempt_number}</span>
          </div>
          <p className="mt-1 font-mono text-2xs text-muted-text">
            {attempt.as_of_date ? `EOD ${fmtDate(attempt.as_of_date)} · ` : ""}started {fmtDateTime(attempt.started_at)} IST
            {durationSeconds !== null ? ` · ${durationSeconds.toFixed(1)}s` : ""}
          </p>
        </div>
        <Button className="ml-auto" onClick={() => openChart(attempt.symbol)} size="sm" type="button" variant="outline">
          <LineChartIcon data-icon="inline-start" /> Chart
        </Button>
      </header>

      <ReasonPanel attempt={attempt} onOpenProposal={onOpenProposal} />

      {Object.keys(structured).length > 0 && (
        <Panel title="Gemini audit">
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-1.5">
              {chips
                .filter(([, value]) => value !== undefined && value !== null)
                .map(([label, value]) => (
                  <span className="rounded border border-border-soft bg-field px-2 py-0.5 text-2xs" key={label}>
                    <span className="text-muted-text">{label}: </span>
                    <b className="font-semibold text-fg2 capitalize">{detailValue(value)}</b>
                  </span>
                ))}
            </div>
            <p className="text-xs leading-relaxed text-fg2">{detailValue(structured.evidence_summary)}</p>
            {redFlags.length > 0 && (
              <div className="rounded-md border border-wa/30 bg-wa-soft px-3 py-2 text-xs text-wa">
                <SectionLabel className="text-wa">Red flags</SectionLabel>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {redFlags.map((flag) => (
                    <li key={flag}>{flag}</li>
                  ))}
                </ul>
              </div>
            )}
            {assessments.length > 0 && (
              <div>
                <SectionLabel>Gemini actions on Python candidates</SectionLabel>
                <ul className="mt-1 space-y-0.5 text-xs text-fg2">
                  {assessments.map((row, index) => (
                    <li key={row.index ?? index}>
                      <span className="font-mono text-muted-text">#{row.index ?? index + 1}</span> {row.action ?? "—"}
                      {row.merge_with_index != null && ` → #${row.merge_with_index}`}
                      {row.note && <span className="text-muted-text"> — {row.note}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Panel>
      )}

      <Panel title="Charts">
        <ProposalCharts
          baseUrl={`${API_BASE_URL}/automation/attempts/${attempt.id}/charts`}
          rendererVersion={attempt.renderer_version}
          symbol={shortSymbol(attempt.symbol)}
        />
      </Panel>

      <div className="rounded-md border border-border-soft bg-field px-4 py-3">
        <div className="grid gap-x-6 gap-y-1 font-mono text-2xs text-muted-text md:grid-cols-2">
          {(
            [
              ["Model", attempt.model],
              ["Source hash", attempt.source_hash],
              ["Geometry", attempt.geometry_version],
              ["Prompt / schema", `${attempt.prompt_version} / ${attempt.schema_version}`],
              ["Risk policy", `v${attempt.risk_policy_version}`],
              ["Provider request", attempt.provider_request_id ?? "—"],
            ] as [string, string][]
          ).map(([label, value]) => (
            <div className="flex min-w-0 gap-2" key={label}>
              <span className="flex-none">{label}</span>
              <span className="truncate text-fg2" title={value}>
                {value}
              </span>
            </div>
          ))}
        </div>
        {onOpenRun && (
          <button className="link-act mt-2 !px-0" onClick={() => onOpenRun(attempt.automation_run_id)} type="button">
            Open the generation run →
          </button>
        )}
      </div>
    </div>
  )
}

/** Deep-link route /proposals/attempts/:attemptId. */
export function AttemptDetailPage() {
  const { attemptId } = useParams<{ attemptId: string }>()
  const navigate = useNavigate()
  return (
    <section className="view h-full">
      <div className="flex flex-none items-center gap-2 border-b border-border px-5 py-2">
        <Button onClick={() => navigate("/proposals?tab=rejected")} size="sm" type="button" variant="ghost">
          <ArrowLeftIcon data-icon="inline-start" /> Proposals
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl p-5">
          {attemptId && (
            <AttemptDetail
              attemptId={attemptId}
              onOpenProposal={(id) => navigate(`/proposals/${id}`)}
              onOpenRun={(runId) => navigate(`/proposals?tab=runs&run=${runId}`)}
            />
          )}
        </div>
      </div>
    </section>
  )
}
