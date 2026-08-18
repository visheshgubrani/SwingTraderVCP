import { useState } from "react"
import { AlertCircleIcon, CheckCircle2Icon, ChevronDownIcon, Clock3Icon, XCircleIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { API_BASE_URL } from "@/lib/api"
import {
  useProposalGenerationResults,
  type ProposalGenerationAttempt,
} from "./api"

interface ProposalGenerationResultsProps {
  automationRunId: string | null
}

function statusBadge(attempt: ProposalGenerationAttempt) {
  if (attempt.status === "valid") return <Badge variant="default">Generated</Badge>
  if (attempt.status === "uncertain") return <Badge variant="secondary">Uncertain</Badge>
  if (attempt.status === "running") return <Badge variant="outline">Running</Badge>
  if (attempt.status === "timed_out") return <Badge variant="secondary">Timed out</Badge>
  if (attempt.status === "failed") return <Badge variant="destructive">Failed</Badge>
  return <Badge variant="destructive">Rejected</Badge>
}

function outcomeIcon(status: ProposalGenerationAttempt["status"]) {
  if (status === "valid") return <CheckCircle2Icon aria-hidden="true" className="text-emerald-400" />
  if (status === "uncertain" || status === "running" || status === "timed_out") {
    return <Clock3Icon aria-hidden="true" className="text-amber-300" />
  }
  return <XCircleIcon aria-hidden="true" className="text-rose-400" />
}

function chartUrl(runId: string, attemptId: string, chart: "context" | "detail") {
  return `${API_BASE_URL}/automation/proposal-batches/${runId}/generation-results/${attemptId}/charts/${chart}`
}

export function ProposalGenerationResults({ automationRunId }: ProposalGenerationResultsProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const { data, error, isLoading } = useProposalGenerationResults(automationRunId)

  if (!automationRunId) {
    return (
      <Alert className="mx-4 mt-3 bg-card/40 font-mono text-xs">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>Generation ledger</AlertTitle>
        <AlertDescription className="text-[10px]">
          Run Generate proposals to audit each candidate outcome, validation reason, and source chart.
        </AlertDescription>
      </Alert>
    )
  }

  const isTerminal = data?.status === "completed" || data?.status === "timed_out" || data?.status === "failed"

  return (
    <section className="mx-4 mt-3 overflow-hidden rounded-lg border border-border/70 bg-card/45 font-mono text-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 bg-muted/20 px-3 py-2.5">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-foreground">
            {data?.status === "running" ? <Spinner aria-hidden="true" /> : <span className="text-primary">/</span>}
            Generation ledger
            {data?.status === "running" ? <Badge variant="outline">Live</Badge> : null}
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            {isLoading
              ? "Reading audited attempts…"
              : error instanceof Error
                ? error.message
                : `Run ${automationRunId.slice(0, 8)} · ${data?.candidates_processed ?? 0}/${data?.candidates_total ?? 0} candidates processed`}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
          <Badge variant="default">Generated {data?.proposals_generated ?? 0}</Badge>
          <Badge variant="destructive">Rejected {data?.proposals_rejected ?? 0}</Badge>
          <Badge variant="secondary">Uncertain {data?.proposals_uncertain ?? 0}</Badge>
          <Badge variant="destructive">Failed {data?.proposals_failed ?? 0}</Badge>
          {data?.status === "timed_out" ? <Badge variant="secondary">Batch timed out</Badge> : null}
        </div>
      </div>

      {data?.error_message ? (
        <div className="border-b border-rose-500/20 bg-rose-500/5 px-3 py-2 text-[10px] text-rose-300">
          {data.error_message}
        </div>
      ) : null}

      {data?.results.length ? (
        <div className="divide-y divide-border/50">
          {data.results.map((attempt) => {
            const expanded = expandedId === attempt.id
            const verdict = attempt.structured_output?.verdict
            const confidence = attempt.structured_output?.confidence
            return (
              <div key={attempt.id} className="px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="flex items-center gap-1.5 font-semibold text-foreground">
                    {outcomeIcon(attempt.status)}
                    {attempt.symbol}
                  </span>
                  {statusBadge(attempt)}
                  {verdict ? <Badge variant="outline">Gemini {verdict}</Badge> : null}
                  {confidence !== undefined ? (
                    <span className="text-[10px] text-muted-foreground">
                      confidence {(Number(confidence) * 100).toFixed(0)}%
                    </span>
                  ) : null}
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    attempt {attempt.attempt_number}
                  </span>
                  <Button
                    aria-expanded={expanded}
                    onClick={() => setExpandedId(expanded ? null : attempt.id)}
                    size="xs"
                    type="button"
                    variant="ghost"
                  >
                    {expanded ? "Hide detail" : "View detail"}
                    <ChevronDownIcon aria-hidden="true" data-icon="inline-end" className={expanded ? "rotate-180" : ""} />
                  </Button>
                </div>
                {attempt.error_message ? (
                  <div className="mt-1 pl-6 text-[10px] leading-relaxed text-rose-300">
                    <span className="text-rose-400/70">{attempt.error_type ?? "validation"}:</span>{" "}
                    {attempt.error_message}
                  </div>
                ) : null}
                {expanded ? (
                  <div className="mt-3 grid gap-3 border-t border-border/50 pt-3 lg:grid-cols-2">
                    <div>
                      <div className="mb-1 text-[10px] uppercase text-muted-foreground">Frozen context · 252 sessions</div>
                      <img
                        alt={`${attempt.symbol} frozen context chart`}
                        className="w-full rounded-md border border-border/60 bg-black"
                        loading="lazy"
                        src={chartUrl(automationRunId, attempt.id, "context")}
                      />
                    </div>
                    <div>
                      <div className="mb-1 text-[10px] uppercase text-muted-foreground">Inference detail · 126 sessions</div>
                      <img
                        alt={`${attempt.symbol} inference detail chart`}
                        className="w-full rounded-md border border-border/60 bg-black"
                        loading="lazy"
                        src={chartUrl(automationRunId, attempt.id, "detail")}
                      />
                    </div>
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      ) : (
        <div className="px-3 py-4 text-[10px] text-muted-foreground">
          {isLoading || !isTerminal ? "Attempts will appear here as the worker starts each chart." : "No audited attempts were recorded for this batch."}
        </div>
      )}
    </section>
  )
}
