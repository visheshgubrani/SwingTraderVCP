import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { useQueryClient } from "@tanstack/react-query"
import {
  AlertCircleIcon,
  ChevronDownIcon,
  HistoryIcon,
  LayersIcon,
  PlayIcon,
  SearchIcon,
  SparklesIcon,
  XCircleIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
  useRejectedAttempts,
  useProposalBatch,
  useProposalBatches,
  useTriggerProposalBatch,
} from "./api"
import { MarketContextPanel } from "./MarketContextPanel"
import { ProposalGenerationResults } from "./ProposalGenerationResults"

export function ProposalInbox() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<string>("pending_approval")
  const [symbolSearch, setSymbolSearch] = useState<string>("")
  const [selectedRunId, setSelectedRunId] = useState<string>("latest")
  const [showMarketContext, setShowMarketContext] = useState<boolean>(false)
  const [showGenerationLedger, setShowGenerationLedger] = useState<boolean>(false)
  const queryClient = useQueryClient()

  const { data: pastRuns = [] } = useProposalBatches(30)
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

  const effectiveRunId = useMemo(() => {
    if (selectedRunId === "latest") {
      return proposalBatch.data?.automation_run_id ?? (pastRuns[0]?.id ?? null)
    }
    if (selectedRunId === "all") {
      return null
    }
    return selectedRunId
  }, [selectedRunId, proposalBatch.data?.automation_run_id, pastRuns])

  const isRejectedTab = statusFilter === "system_rejected"
  const isEntryExpiredTab = statusFilter === "entry_expired"

  const proposalQueryParams = useMemo(() => ({
    symbol: symbolSearch.trim() || null,
    automationRunId: selectedRunId !== "all" && selectedRunId !== "latest" ? selectedRunId : null,
    entryState: isEntryExpiredTab ? "expired" : null,
  }), [symbolSearch, selectedRunId, isEntryExpiredTab])

  const { data: rawProposals = [], isLoading: isProposalsLoading, error: proposalsError } = useTradeProposals(
    isRejectedTab ? "all" : isEntryExpiredTab ? "approved" : statusFilter,
    proposalQueryParams,
  )

  const { data: rawRejected = [], isLoading: isRejectedLoading, error: rejectedError } = useRejectedAttempts({
    status: "all",
    symbol: symbolSearch.trim() || null,
    automationRunId: selectedRunId !== "all" && selectedRunId !== "latest" ? selectedRunId : null,
  })

  const proposals = useMemo(() => {
    if (!symbolSearch.trim()) return rawProposals
    const query = symbolSearch.trim().toUpperCase()
    return rawProposals.filter((p) => p.symbol.toUpperCase().includes(query))
  }, [rawProposals, symbolSearch])

  const rejectedAttempts = useMemo(() => {
    if (!symbolSearch.trim()) return rawRejected
    const query = symbolSearch.trim().toUpperCase()
    return rawRejected.filter((a) => a.symbol.toUpperCase().includes(query))
  }, [rawRejected, symbolSearch])

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
      void queryClient.invalidateQueries({ queryKey: ["automation", "rejected-attempts"] })
      void queryClient.invalidateQueries({ queryKey: ["automation", "proposal-batches"] })
    }
    previousBatchRef.current = { runId, status }
  }, [proposalBatch.data?.automation_run_id, proposalBatch.data?.status, queryClient])

  const templateBadges: Record<string, string> = {
    single: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    two_leg: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    three_leg_front: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    three_leg_balanced: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  }

  const entryStateBadges: Record<string, { label: string; className: string }> = {
    armed: { label: "ARMED", className: "bg-amber-500/10 text-amber-400 border-amber-500/30" },
    trigger_observed: { label: "TRIGGERED", className: "bg-amber-500/10 text-amber-400 border-amber-500/30" },
    executing: { label: "EXECUTING", className: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
    filled: { label: "FILLED", className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" },
    expired: { label: "ENTRY EXPIRED", className: "bg-rose-500/10 text-rose-300 border-rose-500/30" },
  }

  return (
    <div className="flex h-full w-full flex-col overflow-y-auto bg-background font-mono text-xs text-foreground min-h-0">
      {/* Top Header & Supervisor Monitor */}
      <div className="sticky top-0 z-20 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border/80 bg-card/95 px-4 py-3 backdrop-blur shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-primary">
            <SparklesIcon className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-foreground">
              VCP Trade Proposals & Entry Supervisor
            </h1>
            <p className="text-[11px] text-muted-foreground">
              Screening → serial Gemini pattern analysis → human decision → deterministic execution.
            </p>
          </div>
        </div>

        {/* Action Controls & Supervisor Indicator */}
        <div className="flex flex-wrap items-center gap-3">
          <div
            className="max-w-72 truncate text-[10px] text-muted-foreground hidden lg:block"
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
            {batchRunning ? "Generating" : "Generate batch"}
          </Button>

          <div className="flex items-center gap-3 rounded-lg border border-border/60 bg-muted/20 px-3 py-1.5 text-[11px]">
            <div className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${supervisorActive ? "bg-emerald-500 animate-pulse" : "bg-rose-500"}`} />
              <span className="text-muted-foreground">Supervisor:</span>
              <span className={supervisorActive ? "font-bold text-emerald-400" : "font-bold text-rose-400"}>
                {supervisorActive ? "ACTIVE" : "INACTIVE"}
              </span>
            </div>
            <span className="text-muted-foreground/50">|</span>
            <div className="text-muted-foreground">
              Armed: <strong className="text-foreground">{supervisorStatus?.armed_legs_count ?? 0}</strong>
            </div>
          </div>
        </div>
      </div>

      {batchFailed ? (
        <div className="mx-4 mt-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-[10px] text-rose-200">
          {batchMessage}
        </div>
      ) : null}

      {/* Main Content Area */}
      <div className="p-4 space-y-4">
        {/* Run Selector & Collapsible Panels Bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/70 bg-card/60 p-3 shadow-sm">
          <div className="flex items-center gap-2">
            <HistoryIcon className="h-4 w-4 text-primary" />
            <span className="font-semibold text-foreground">Generation Run History:</span>
            <select
              value={selectedRunId}
              onChange={(e) => setSelectedRunId(e.target.value)}
              className="rounded-md border border-border bg-background px-2.5 py-1 text-[11px] text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="latest">Latest Batch / Run</option>
              <option value="all">All Historical Runs & Trades</option>
              {pastRuns.map((run) => {
                const label = run.run_type === "single"
                  ? `Single Stock: ${run.single_symbol ?? "Single"} · ${run.status.toUpperCase()} (${new Date(run.created_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" })})`
                  : `Batch (${run.candidates_total} charts) · ${run.proposals_generated} Gen · ${run.status.toUpperCase()} (${new Date(run.created_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" })})`
                return (
                  <option key={run.id} value={run.id}>
                    {label}
                  </option>
                )
              })}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="xs"
              onClick={() => setShowGenerationLedger(!showGenerationLedger)}
              className="text-[10px] text-muted-foreground hover:text-foreground gap-1"
            >
              <LayersIcon className="h-3 w-3" />
              {showGenerationLedger ? "Hide Batch Ledger" : "View Batch Ledger"}
            </Button>
            <Button
              variant="outline"
              size="xs"
              onClick={() => setShowMarketContext(!showMarketContext)}
              className="text-[10px] text-muted-foreground hover:text-foreground gap-1"
            >
              <ChevronDownIcon className={`h-3 w-3 transition-transform ${showMarketContext ? "" : "-rotate-90"}`} />
              {showMarketContext ? "Hide Market Context" : "P9 Market Context"}
            </Button>
          </div>
        </div>

        {/* Optional Collapsible P9 Context & Generation Ledger */}
        {showMarketContext && <MarketContextPanel />}

        {showGenerationLedger && (
          <ProposalGenerationResults
            automationRunId={effectiveRunId}
          />
        )}

        {(supervisorStatus?.recent_allocation_events ?? [])
          .filter((event) => event.context_gate_reasons?.length)
          .slice(0, 2)
          .map((event) => (
            <div key={event.id} className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[10px] text-amber-200">
              Allocation {event.event_type.replaceAll("_", " ")} · {event.market_context_mode ?? "breaker"} · {event.context_gate_reasons.join(", ")}
              {event.context_adjusted_risk_ceiling !== null && ` · ceiling ₹${Number(event.context_adjusted_risk_ceiling).toFixed(2)}`}
            </div>
          ))}

        {conflicts.map((conflict) => (
          <div key={conflict.id} className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
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
                  Select {candidate.symbol} L{candidate.leg_index} · {Number(candidate.conservative_rr).toFixed(2)}R
                </Button>
              ))}
            </div>
          </div>
        ))}

        {/* Proposals / Rejected Tabs and Search Bar */}
        <div className="space-y-3 pt-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {[
                { key: "pending_approval", label: "Pending Approval" },
                { key: "approved", label: "Approved" },
                { key: "entry_expired", label: "Entry Expired" },
                { key: "system_rejected", label: "Rejected by System" },
                { key: "rejected", label: "Rejected by Operator" },
                { key: "expired_unapproved", label: "Expired" },
                { key: "all", label: "All Trades" },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setStatusFilter(tab.key)}
                  className={`rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors ${
                    statusFilter === tab.key
                      ? "bg-primary text-primary-foreground shadow-sm font-bold"
                      : "text-muted-foreground hover:bg-muted/30 hover:text-foreground"
                  }`}
                >
                  {tab.label}
                  {tab.key === "system_rejected" && rejectedAttempts.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-rose-500/20 px-1.5 py-0.5 text-[9px] text-rose-300 font-bold">
                      {rejectedAttempts.length}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Symbol Search Bar */}
            <div className="relative w-48 sm:w-64">
              <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                type="text"
                placeholder="Search symbol..."
                value={symbolSearch}
                onChange={(e) => setSymbolSearch(e.target.value)}
                className="h-8 pl-8 text-xs font-mono"
              />
            </div>
          </div>

          {/* REJECTED BY SYSTEM TABLE */}
          {isRejectedTab ? (
            isRejectedLoading ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-border/60 bg-card text-muted-foreground">
                <div className="flex flex-col items-center gap-2">
                  <Spinner className="h-5 w-5 text-primary" />
                  <span>Loading system-rejected trade candidates…</span>
                </div>
              </div>
            ) : rejectedError ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-400">
                Error loading rejected trade candidates
              </div>
            ) : rejectedAttempts.length === 0 ? (
              <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-lg border border-border/60 bg-card/40 text-muted-foreground p-4 text-center">
                <AlertCircleIcon className="h-8 w-8 text-muted-foreground/40" />
                <p>
                  {symbolSearch
                    ? `No system-rejected candidates matching symbol "${symbolSearch}".`
                    : "No candidate setups were rejected by the system rules for this selection."}
                </p>
              </div>
            ) : (
              <div className="rounded-lg border border-border/60 bg-card overflow-hidden shadow-sm">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-border/60 bg-muted/30 text-[10px] text-muted-foreground uppercase tracking-wider">
                      <th className="py-2.5 px-3 font-semibold">Symbol</th>
                      <th className="py-2.5 px-3 font-semibold">Gemini Verdict</th>
                      <th className="py-2.5 px-3 font-semibold">Proposed Pivot</th>
                      <th className="py-2.5 px-3 font-semibold">Proposed SL</th>
                      <th className="py-2.5 px-3 font-semibold">System Rejection Reason</th>
                      <th className="py-2.5 px-3 font-semibold">Date</th>
                      <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40 text-[11px]">
                    {rejectedAttempts.map((attempt) => {
                      const structured = attempt.structured_output ?? {}
                      const pivot = structured.pivot_price != null ? Number(structured.pivot_price) : null
                      const verdict = structured.verdict ?? attempt.status

                      return (
                        <tr
                          key={attempt.id}
                          onClick={() => navigate(`/proposals/attempts/${attempt.id}`)}
                          className="cursor-pointer hover:bg-muted/20 transition-colors group"
                        >
                          <td className="py-2.5 px-3 font-bold text-foreground group-hover:text-primary transition-colors">
                            <div className="flex items-center gap-1.5">
                              <XCircleIcon className="h-3.5 w-3.5 text-rose-400 shrink-0" />
                              {attempt.symbol}
                            </div>
                          </td>
                          <td className="py-2.5 px-3">
                            <div className="flex items-center gap-1.5">
                              <Badge variant="outline" className="text-[10px]">
                                {String(verdict).toUpperCase()}
                              </Badge>
                            </div>
                          </td>
                          <td className="py-2.5 px-3 font-semibold text-foreground">
                            {pivot != null ? `₹${pivot.toFixed(2)}` : "-"}
                          </td>
                          <td className="py-2.5 px-3 text-rose-400">
                            {attempt.error_type === "proposal_geometry_invalid" ? (
                              <span className="font-semibold">{attempt.error_message?.split(";")[0] ?? "Invalid SL"}</span>
                            ) : (
                              "-"
                            )}
                          </td>
                          <td className="py-2.5 px-3 text-rose-300/90 max-w-xs truncate" title={attempt.error_message || ""}>
                            <span className="font-semibold text-rose-400">{attempt.error_type ?? "rejected"}:</span>{" "}
                            {attempt.error_message || "Rejected by deterministic risk rules"}
                          </td>
                          <td className="py-2.5 px-3 text-muted-foreground">
                            {attempt.as_of_date || new Date(attempt.started_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" })}
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-6 px-2.5 text-[10px] font-bold text-rose-400 border-rose-500/30 hover:bg-rose-500/10"
                              onClick={(e) => {
                                e.stopPropagation()
                                navigate(`/proposals/attempts/${attempt.id}`)
                              }}
                            >
                              View Details
                            </Button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            /* PROPOSALS TABLE (Pending, Approved, Rejected by Operator, Expired, All) */
            isProposalsLoading ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-border/60 bg-card text-muted-foreground">
                <div className="flex flex-col items-center gap-2">
                  <Spinner className="h-5 w-5 text-primary" />
                  <span>Loading trade proposals…</span>
                </div>
              </div>
            ) : proposalsError ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-400">
                Error loading trade proposals
              </div>
            ) : proposals.length === 0 ? (
              <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-lg border border-border/60 bg-card/40 text-muted-foreground p-4 text-center">
                <AlertCircleIcon className="h-8 w-8 text-muted-foreground/40" />
                <p>
                  {symbolSearch
                    ? `No proposals found matching symbol "${symbolSearch}".`
                    : statusFilter === "pending_approval"
                      ? "No proposals are waiting for approval. Generate a batch from the latest scan to review candidates."
                      : `No proposals found for ${statusFilter.replaceAll("_", " ")}.`}
                </p>
              </div>
            ) : (
              <div className="rounded-lg border border-border/60 bg-card overflow-hidden shadow-sm">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-border/60 bg-muted/30 text-[10px] text-muted-foreground uppercase tracking-wider">
                      <th className="py-2.5 px-3 font-semibold">Symbol</th>
                      <th className="py-2.5 px-3 font-semibold">Template</th>
                      <th className="py-2.5 px-3 font-semibold">Pivot Entry</th>
                      <th className="py-2.5 px-3 font-semibold">Stop Loss</th>
                      <th className="py-2.5 px-3 font-semibold">Target 1</th>
                      <th className="py-2.5 px-3 font-semibold">Risk Budget</th>
                      <th className="py-2.5 px-3 font-semibold">Session Date</th>
                      <th className="py-2.5 px-3 font-semibold">Entry</th>
                      <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40 text-[11px]">
                    {proposals.map((p) => {
                      const isPending = p.status === "pending_approval"
                      return (
                        <tr
                          key={p.id}
                          onClick={() => navigate(`/proposals/${p.id}`)}
                          className="cursor-pointer hover:bg-muted/20 transition-colors group"
                        >
                          <td className="py-2.5 px-3 font-bold text-foreground group-hover:text-primary transition-colors">
                            <div className="flex items-center gap-1.5">
                              {p.symbol}
                              {p.live_eligible ? (
                                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" title="Live Eligible" />
                              ) : null}
                            </div>
                          </td>
                          <td className="py-2.5 px-3">
                            <Badge
                              variant="outline"
                              className={templateBadges[p.entry_template] || ""}
                            >
                              {p.entry_template.toUpperCase()}
                            </Badge>
                          </td>
                          <td className="py-2.5 px-3 font-semibold text-foreground">
                            ₹{Number(p.pivot_price).toFixed(2)}
                          </td>
                          <td className="py-2.5 px-3 text-rose-400">
                            ₹{Number(p.initial_stop).toFixed(2)} (
                            {Number(p.stop_distance_pct).toFixed(2)}%)
                          </td>
                          <td className="py-2.5 px-3 text-emerald-400 font-semibold">
                            ₹{Number(p.t1).toFixed(2)}
                          </td>
                          <td className="py-2.5 px-3 text-muted-foreground">
                            {Number(p.risk_budget_pct).toFixed(1)}% (
                            {p.leg_count} legs)
                          </td>
                          <td className="py-2.5 px-3 text-muted-foreground">
                            {p.entry_session_date}
                          </td>
                          <td className="py-2.5 px-3">
                            {p.entry_state && entryStateBadges[p.entry_state] ? (
                              <Badge
                                variant="outline"
                                className={entryStateBadges[p.entry_state].className}
                              >
                                {entryStateBadges[p.entry_state].label}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground/50">-</span>
                            )}
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <Button
                              variant={isPending ? "default" : "outline"}
                              size="sm"
                              className="h-6 px-2.5 text-[10px] font-bold"
                              onClick={(e) => {
                                e.stopPropagation()
                                navigate(`/proposals/${p.id}`)
                              }}
                            >
                              {isPending ? "Review Plan" : "View Details"}
                            </Button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  )
}

