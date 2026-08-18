import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  AlertCircleIcon,
  PlayIcon,
  SparklesIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  defaultScanRunId,
  productionScanRuns,
  useScanRuns,
} from "@/features/screener/api"
import { ApiError } from "@/lib/api"
import {
  useEntrySupervisorStatus,
  useCapacityConflicts,
  useResolveCapacityConflict,
  useTradeProposals,
  useProposalBatch,
  useTriggerProposalBatch,
  type TradeProposalItem,
} from "./api"
import { ProposalDetailModal } from "./ProposalDetailModal"
import { MarketContextPanel } from "./MarketContextPanel"
import { ProposalGenerationResults } from "./ProposalGenerationResults"

export function ProposalInbox() {
  const [statusFilter, setStatusFilter] = useState<string>("pending_approval")
  const [selectedProposal, setSelectedProposal] = useState<TradeProposalItem | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const queryClient = useQueryClient()

  const { data: proposals = [], isLoading, error } = useTradeProposals(statusFilter)
  const { data: supervisorStatus } = useEntrySupervisorStatus()
  const { data: conflicts = [] } = useCapacityConflicts()
  const resolveConflict = useResolveCapacityConflict()
  const scanRuns = useScanRuns()
  const latestScanId = defaultScanRunId(productionScanRuns(scanRuns.data))
  const latestScan = productionScanRuns(scanRuns.data).find(
    (run) => run.id === latestScanId,
  )
  const proposalBatch = useProposalBatch(latestScanId)
  const triggerBatch = useTriggerProposalBatch()
  const supervisorActive = supervisorStatus?.status === "active"
  const batchRunning =
    proposalBatch.data?.status === "running" || triggerBatch.isPending
  const batchMessage = useMemo(() => {
    if (triggerBatch.error instanceof ApiError || triggerBatch.error instanceof Error) {
      return triggerBatch.error.message
    }
    if (proposalBatch.data?.status === "running") {
      return `Generating ${proposalBatch.data.candidates_processed}/${proposalBatch.data.candidates_total} · ${proposalBatch.data.proposals_generated} proposals`
    }
    if (proposalBatch.data?.status === "completed") {
      return `Last batch: ${proposalBatch.data.proposals_generated} proposals from ${proposalBatch.data.candidates_processed} charts`
    }
    if (proposalBatch.data?.status === "timed_out") {
      return (
        proposalBatch.data.error_message ??
        "Last batch timed out before any charts were processed."
      )
    }
    if (proposalBatch.data?.status === "failed") {
      return proposalBatch.data.error_message ?? "Last batch failed."
    }
    return latestScan
      ? `Latest scan ${latestScan.as_of_date ?? ""} · ${latestScan.passing_count} setups`
      : "No completed personal scan yet"
  }, [latestScan, proposalBatch.data, triggerBatch.error])
  const batchFailed =
    proposalBatch.data?.status === "timed_out" ||
    proposalBatch.data?.status === "failed"

  const previousBatchRef = useRef<{ runId: string | null; status: string | null }>({
    runId: null,
    status: null,
  })
  useEffect(() => {
    const runId = proposalBatch.data?.automation_run_id ?? null
    const status = proposalBatch.data?.status ?? null
    const previous = previousBatchRef.current
    const terminal = status === "completed" || status === "timed_out" || status === "failed"
    if (terminal && (previous.runId !== runId || previous.status !== status)) {
      void queryClient.invalidateQueries({
        queryKey: ["automation", "proposal-generation-results", runId],
      })
      void queryClient.invalidateQueries({ queryKey: ["trade-proposals"] })
    }
    previousBatchRef.current = { runId, status }
  }, [proposalBatch.data?.automation_run_id, proposalBatch.data?.status, queryClient])

  const templateBadges: Record<string, string> = {
    single: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    two_leg: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    three_leg_front: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    three_leg_balanced: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background font-mono text-xs text-foreground">
      {/* Top Header & Supervisor Monitor */}
      <div className="flex shrink-0 items-center justify-between border-b border-border/80 bg-card/60 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-primary">
            <SparklesIcon className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-foreground">
              VCP Trade Proposals & Entry Supervisor
            </h1>
            <p className="text-[11px] text-muted-foreground">
              Generate a serial Gemini batch from the latest scan, then approve or reject each immutable proposal.
            </p>
          </div>
        </div>

        {/* Live Supervisor Indicator */}
        <div className="flex items-center gap-3">
          <div
            className="max-w-80 truncate text-[10px] text-muted-foreground"
            title={batchMessage}
          >
            {batchMessage}
          </div>
          <Button
            className="h-8 gap-1.5 font-bold uppercase"
            disabled={batchRunning || !latestScan || latestScan.status !== "succeeded"}
            onClick={() => triggerBatch.mutate(latestScanId)}
            size="sm"
            type="button"
          >
            {batchRunning ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <PlayIcon className="size-3.5 fill-current" />
            )}
            {batchRunning ? "Generating" : "Generate proposals"}
          </Button>
          <div className="flex items-center gap-3 rounded-lg border border-border/60 bg-muted/20 px-3 py-1.5 text-[11px]">
          <div className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${supervisorActive ? "bg-emerald-500 animate-pulse" : "bg-rose-500"}`} />
            <span className="text-muted-foreground">Entry Supervisor:</span>
            <span className={supervisorActive ? "font-bold text-emerald-400" : "font-bold text-rose-400"}>
              {supervisorActive ? "ACTIVE" : "INACTIVE"}
            </span>
          </div>
          <span className="text-muted-foreground/50">|</span>
          <div className="text-muted-foreground">
            Armed Legs:{" "}
            <strong className="text-foreground">
              {supervisorStatus?.armed_legs_count ?? 0}
            </strong>
          </div>
          </div>
        </div>
      </div>

      {batchFailed ? (
        <div className="mx-4 mt-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[10px] text-rose-200">
          {batchMessage}
        </div>
      ) : null}

      <MarketContextPanel />

      <ProposalGenerationResults
        automationRunId={proposalBatch.data?.automation_run_id ?? null}
      />

      {(supervisorStatus?.recent_allocation_events ?? [])
        .filter((event) => event.context_gate_reasons?.length)
        .slice(0, 3)
        .map((event) => (
          <div key={event.id} className="mx-4 mt-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[10px] text-amber-200">
            Allocation {event.event_type.replaceAll("_", " ")} · {event.market_context_mode ?? "breaker"} · {event.context_gate_reasons.join(", ")}
            {event.context_adjusted_risk_ceiling !== null && ` · ceiling ₹${Number(event.context_adjusted_risk_ceiling).toFixed(2)}`}
          </div>
        ))}

      {/* Filter Tabs */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 bg-card/30 px-4 py-2">
        {[
          { key: "pending_approval", label: "Pending Approval" },
          { key: "approved", label: "Approved" },
          { key: "expired_unapproved", label: "Expired" },
          { key: "rejected", label: "Human rejected" },
          { key: "all", label: "All Proposals" },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setStatusFilter(tab.key)}
            className={`rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors ${
              statusFilter === tab.key
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:bg-muted/30 hover:text-foreground"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {conflicts.map((conflict) => (
        <div key={conflict.id} className="mx-4 mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <div className="font-semibold text-amber-300">Capacity tie needs your decision</div>
              <div className="text-[10px] text-muted-foreground">Choose one immutable proposal or skip all. The signal expires quickly.</div>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={resolveConflict.isPending}
              onClick={() => resolveConflict.mutate({ id: conflict.id, chosenLegId: null })}
            >
              Skip all
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {conflict.candidates.map((candidate) => (
              <Button
                key={candidate.leg_id}
                size="sm"
                disabled={resolveConflict.isPending}
                onClick={() => resolveConflict.mutate({ id: conflict.id, chosenLegId: candidate.leg_id })}
              >
                Select {candidate.symbol} L{candidate.leg_index} · {(Number(candidate.confidence) * 100).toFixed(0)}% · {Number(candidate.conservative_rr).toFixed(2)}R
              </Button>
            ))}
          </div>
        </div>
      ))}

      {/* Main Proposal Table / Content */}
      <div className="flex-1 overflow-auto p-4">
        {isLoading ? (
          <div className="flex h-64 items-center justify-center text-muted-foreground">
            Loading proposals...
          </div>
        ) : error ? (
          <div className="flex h-64 items-center justify-center text-rose-400">
            Error loading trade proposals
          </div>
        ) : proposals.length === 0 ? (
          <div className="flex h-64 flex-col items-center justify-center gap-2 text-muted-foreground">
            <AlertCircleIcon className="h-8 w-8 text-muted-foreground/40" />
            <p>
              {statusFilter === "pending_approval"
                ? "No immutable proposals are waiting for your decision. Generate a batch to create the next review set."
                : statusFilter === "rejected"
                  ? "No proposals have been rejected by the operator yet. Model and Python rejections live in the generation ledger above."
                  : `No immutable proposals found for ${statusFilter.replaceAll("_", " ")}.`}
            </p>
          </div>
        ) : (
          <div className="rounded-lg border border-border/60 bg-card overflow-hidden">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border/60 bg-muted/30 text-[10px] text-muted-foreground uppercase tracking-wider">
                  <th className="py-2.5 px-3 font-semibold">Symbol</th>
                  <th className="py-2.5 px-3 font-semibold">Template</th>
                  <th className="py-2.5 px-3 font-semibold">Confidence</th>
                  <th className="py-2.5 px-3 font-semibold">Pivot Entry</th>
                  <th className="py-2.5 px-3 font-semibold">Stop Loss</th>
                  <th className="py-2.5 px-3 font-semibold">Target 1</th>
                  <th className="py-2.5 px-3 font-semibold">Risk Budget</th>
                  <th className="py-2.5 px-3 font-semibold">Target Session</th>
                  <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40 text-[11px]">
                {proposals.map((p) => {
                  const isPending = p.status === "pending_approval"
                  return (
                    <tr
                      key={p.id}
                      onClick={() => {
                        setSelectedProposal(p)
                        setDetailOpen(true)
                      }}
                      className="cursor-pointer hover:bg-muted/20 transition-colors"
                    >
                      <td className="py-2.5 px-3 font-bold text-foreground">
                        {p.symbol}
                      </td>
                      <td className="py-2.5 px-3">
                        <Badge
                          variant="outline"
                          className={templateBadges[p.entry_template] || ""}
                        >
                          {p.entry_template.toUpperCase()}
                        </Badge>
                      </td>
                      <td className="py-2.5 px-3 text-muted-foreground">
                        {(Number(p.confidence) * 100).toFixed(0)}%
                      </td>
                      <td className="py-2.5 px-3 font-semibold text-foreground">
                        ₹{Number(p.pivot_price).toFixed(2)}
                      </td>
                      <td className="py-2.5 px-3 text-rose-400">
                        ₹{Number(p.initial_stop).toFixed(2)} (
                        {Number(p.stop_distance_pct).toFixed(2)}%)
                      </td>
                      <td className="py-2.5 px-3 text-emerald-400">
                        ₹{Number(p.t1).toFixed(2)}
                      </td>
                      <td className="py-2.5 px-3 text-muted-foreground">
                        {Number(p.risk_budget_pct).toFixed(1)}% (
                        {p.leg_count} legs)
                      </td>
                      <td className="py-2.5 px-3 text-muted-foreground">
                        {p.entry_session_date}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        {isPending ? (
                          <Button variant="outline" size="sm" className="h-6 px-2 text-[10px]">
                            Review charts
                          </Button>
                        ) : (
                          <Badge
                            variant={
                              p.status === "approved"
                                ? "default"
                                : p.status === "expired_unapproved"
                                  ? "secondary"
                                  : "outline"
                            }
                            className="text-[9px] uppercase"
                          >
                            {p.status.replace("_", " ")}
                          </Badge>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Detail Modal */}
      <ProposalDetailModal
        proposal={selectedProposal}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />
    </div>
  )
}
