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
  useFormingPatterns,
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
  const selectedBatchGenerated = useMemo(() => {
    if (!effectiveRunId) return 0
    if (proposalBatch.data?.automation_run_id === effectiveRunId) {
      return proposalBatch.data.proposals_generated
    }
    return pastRuns.find((run) => run.id === effectiveRunId)?.proposals_generated ?? 0
  }, [effectiveRunId, pastRuns, proposalBatch.data])

  const isRejectedTab = statusFilter === "system_rejected"
  const isEntryExpiredTab = statusFilter === "entry_expired"
  const isFormingTab = statusFilter === "forming"

  const proposalQueryParams = useMemo(() => ({
    symbol: symbolSearch.trim() || null,
    automationRunId: selectedRunId === "all" ? null : effectiveRunId,
    entryState: isEntryExpiredTab ? "expired" : null,
  }), [symbolSearch, selectedRunId, effectiveRunId, isEntryExpiredTab])

  const { data: rawProposals = [], isLoading: isProposalsLoading, error: proposalsError } = useTradeProposals(
    isRejectedTab || isFormingTab ? "all" : isEntryExpiredTab ? "approved" : statusFilter,
    proposalQueryParams,
  )

  const { data: rawRejected = [], isLoading: isRejectedLoading, error: rejectedError } = useRejectedAttempts({
    status: "all",
    symbol: symbolSearch.trim() || null,
    automationRunId: selectedRunId === "all" ? null : effectiveRunId,
  })

  const { data: rawForming = [], isLoading: isFormingLoading, error: formingError } = useFormingPatterns("watching")

  const proposals = useMemo(() => {
    if (!symbolSearch.trim()) return rawProposals
    const query = symbolSearch.trim().toUpperCase()
    return rawProposals.filter((p) => p.symbol.toUpperCase().includes(query))
  }, [rawProposals, symbolSearch])

  const formingPatterns = useMemo(() => {
    if (!symbolSearch.trim()) return rawForming
    const query = symbolSearch.trim().toUpperCase()
    return rawForming.filter((row) => row.symbol.toUpperCase().includes(query))
  }, [rawForming, symbolSearch])

  const rejectedAttempts = useMemo(() => {
    if (!symbolSearch.trim()) return rawRejected
    const query = symbolSearch.trim().toUpperCase()
    return rawRejected.filter((a) => a.symbol.toUpperCase().includes(query))
  }, [rawRejected, symbolSearch])

  const [isPendingTrigger, setIsPendingTrigger] = useState(false)
  const supervisorActive = supervisorStatus?.status === "active"
  const batchRunning =
    proposalBatch.data?.status === "running" || triggerBatch.isPending || isPendingTrigger
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
    single: "bg-green-500/10 text-green-400 border-green-500/20",
    two_leg: "bg-accent/10 text-accent border-accent/20",
    three_leg_front: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    three_leg_balanced: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  }

  const entryStateBadges: Record<string, { label: string; className: string }> = {
    armed: { label: "ARMED", className: "bg-amber-500/10 text-amber-400 border-amber-500/30" },
    trigger_observed: { label: "TRIGGERED", className: "bg-amber-500/10 text-amber-400 border-amber-500/30" },
    waiting_for_reset: { label: "WAITING RESET", className: "bg-orange-500/10 text-orange-400 border-orange-500/30" },
    executing: { label: "EXECUTING", className: "bg-accent/10 text-accent border-accent/30" },
    filled: { label: "FILLED", className: "bg-green-500/10 text-green-400 border-green-500/30" },
    expired: { label: "ENTRY EXPIRED", className: "bg-ko/10 text-ko border-ko/30" },
  }

  return (
    <section className="view h-full">
      {/* Top Header & Supervisor Monitor */}
      <div className="vhead">
        <div>
          <h2>
            Trade Proposals <span className="sub">screening → serial Gemini audit → human decision</span>
          </h2>
          <p className="vmeta">Proposal pipeline · batch runs on the latest succeeded EOD scan</p>
        </div>

        {/* Action Controls & Supervisor Indicator */}
        <div className="flex items-center gap-2">
          <div
            className="mono hidden max-w-72 truncate text-[10px] text-muted-text lg:block"
            title={batchMessage}
          >
            {batchMessage}
          </div>

          <Button
            className="h-8 gap-1.5 font-bold uppercase"
            disabled={batchRunning || !latestScan || latestScan.status !== "succeeded"}
            onClick={() => {
              setIsPendingTrigger(true)
              triggerBatch.mutate(latestScanId, {
                onSettled: () => {
                  setTimeout(() => setIsPendingTrigger(false), 2000)
                },
              })
            }}
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

          <div className="flex items-center gap-2 text-[11px]">
            <div className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${supervisorActive ? "bg-green-500 animate-pulse" : "bg-ko"}`} />
              <span className="text-muted-foreground">Supervisor:</span>
              <span className={supervisorActive ? "font-bold text-green-400" : "font-bold text-ko"}>
                {supervisorActive ? "ACTIVE" : "INACTIVE"}
              </span>
            </div>
            <span className="text-muted-foreground/50">|</span>
            <div className="text-muted-foreground">
              Armed: <strong className="text-foreground">{supervisorStatus?.armed_legs_count ?? 0}</strong>
            </div>
            <span className="text-muted-foreground/50">|</span>
            <div className="text-muted-foreground">
              Awaiting reset:{" "}
              <strong className="text-amber-300">
                {supervisorStatus?.waiting_for_reset_count ?? 0}
              </strong>
            </div>
          </div>
        </div>
      </div>

      {batchFailed ? (
        <div className="flex-none border-b border-ko-soft bg-ko-soft px-5 py-1.5 font-mono text-[10.5px] text-ko">
          {batchMessage}
        </div>
      ) : null}

      {/* Main Content Area */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 pt-2">
        {/* Run Selector & Collapsible Panels Bar */}
        <div className="flex flex-none flex-wrap items-center justify-between gap-3 py-1">
          <div className="flex items-center gap-2">
            <HistoryIcon aria-hidden="true" className="h-3.5 w-3.5 text-muted-text" />
            <span className="mono text-[10px] font-bold uppercase tracking-[0.1em] text-muted-text">Generation Run History</span>
            <select
              value={selectedRunId}
              onChange={(e) => setSelectedRunId(e.target.value)}
              className="filter-select mono"
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
              {showMarketContext ? "Hide Market Context" : "Market Context"}
            </Button>
          </div>
        </div>

        {/* Optional Collapsible Market Context & Generation Ledger */}
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
                { key: "forming", label: "Forming Watch" },
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
                  {tab.key === "forming" && formingPatterns.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[9px] text-amber-300 font-bold">
                      {formingPatterns.length}
                    </span>
                  )}
                  {tab.key === "system_rejected" && rejectedAttempts.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-ko/20 px-1.5 py-0.5 text-[9px] text-ko font-bold">
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

          {/* FORMING WATCH TABLE */}
          {isFormingTab ? (
            isFormingLoading ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-border/60 bg-card text-muted-foreground">
                <div className="flex flex-col items-center gap-2">
                  <Spinner className="h-5 w-5 text-primary" />
                  <span>Loading forming-pattern watches…</span>
                </div>
              </div>
            ) : formingError ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-ko/30 bg-ko/5 text-ko">
                Error loading forming-pattern watches
              </div>
            ) : formingPatterns.length === 0 ? (
              <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-lg border border-border/60 bg-card/40 text-muted-foreground p-4 text-center">
                <AlertCircleIcon className="h-8 w-8 text-muted-foreground/40" />
                <p>
                  {symbolSearch
                    ? `No forming watches matching symbol "${symbolSearch}".`
                    : "No forming VCP watches. Gemini forming classifications appear here until they complete, break down, or expire after 10 NSE sessions."}
                </p>
              </div>
            ) : (
              <div className="rounded-lg border border-border/60 bg-card overflow-hidden shadow-sm">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-border/60 bg-muted/30 text-[10px] text-muted-foreground uppercase tracking-wider">
                      <th className="py-2.5 px-3 font-semibold">Symbol</th>
                      <th className="py-2.5 px-3 font-semibold">State</th>
                      <th className="py-2.5 px-3 font-semibold">First Seen</th>
                      <th className="py-2.5 px-3 font-semibold">Last As-Of</th>
                      <th className="py-2.5 px-3 font-semibold">Next Check</th>
                      <th className="py-2.5 px-3 font-semibold">Python Candidates</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40 text-[11px]">
                    {formingPatterns.map((row) => (
                      <tr key={row.id} className="hover:bg-muted/20 transition-colors">
                        <td className="py-2.5 px-3 font-bold text-foreground">{row.symbol}</td>
                        <td className="py-2.5 px-3">
                          <Badge variant="outline" className="text-[10px] text-amber-300 border-amber-500/30">
                            {row.forming_state.replaceAll("_", " ").toUpperCase()}
                          </Badge>
                        </td>
                        <td className="py-2.5 px-3 text-muted-foreground">{row.first_seen_as_of}</td>
                        <td className="py-2.5 px-3 text-muted-foreground">{row.last_as_of}</td>
                        <td className="py-2.5 px-3 text-muted-foreground">{row.next_check_date}</td>
                        <td className="py-2.5 px-3 text-muted-foreground">{row.python_candidates?.length ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : isRejectedTab ? (
            isRejectedLoading ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-border/60 bg-card text-muted-foreground">
                <div className="flex flex-col items-center gap-2">
                  <Spinner className="h-5 w-5 text-primary" />
                  <span>Loading system-rejected trade candidates…</span>
                </div>
              </div>
            ) : rejectedError ? (
              <div className="flex h-48 items-center justify-center rounded-lg border border-ko/30 bg-ko/5 text-ko">
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
                      <th className="py-2.5 px-3 font-semibold">Classification</th>
                      <th className="py-2.5 px-3 font-semibold">Python / LLM</th>
                      <th className="py-2.5 px-3 font-semibold">System Rejection Reason</th>
                      <th className="py-2.5 px-3 font-semibold">Date</th>
                      <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/40 text-[11px]">
                    {rejectedAttempts.map((attempt) => {
                      const structured = attempt.structured_output ?? {}
                      const classification = structured.classification ?? structured.verdict ?? attempt.status

                      return (
                        <tr
                          key={attempt.id}
                          onClick={() => navigate(`/proposals/attempts/${attempt.id}`)}
                          className="cursor-pointer hover:bg-muted/20 transition-colors group"
                        >
                          <td className="py-2.5 px-3 font-bold text-foreground group-hover:text-primary transition-colors">
                            <div className="flex items-center gap-1.5">
                              <XCircleIcon className="h-3.5 w-3.5 text-ko shrink-0" />
                              {attempt.symbol}
                            </div>
                          </td>
                          <td className="py-2.5 px-3">
                            <div className="flex items-center gap-1.5">
                              <Badge variant="outline" className="text-[10px]">
                                {String(classification).toUpperCase()}
                              </Badge>
                            </div>
                          </td>
                          <td className="py-2.5 px-3 text-muted-foreground">
                            {structured.python_count != null || structured.llm_count != null
                              ? `${structured.python_count ?? "—"} / ${structured.llm_count ?? "—"}`
                              : "—"}
                          </td>
                          <td className="py-2.5 px-3 text-ko/90 max-w-xs truncate" title={attempt.error_message || ""}>
                            <span className="font-semibold text-ko">{attempt.error_type ?? "rejected"}:</span>{" "}
                            {attempt.error_message || "Rejected by deterministic risk rules"}
                          </td>
                          <td className="py-2.5 px-3 text-muted-foreground">
                            {attempt.as_of_date || new Date(attempt.started_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric" })}
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-6 px-2.5 text-[10px] font-bold text-ko border-ko/30 hover:bg-ko/10"
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
              <div className="flex h-48 items-center justify-center rounded-lg border border-ko/30 bg-ko/5 text-ko">
                Error loading trade proposals
              </div>
            ) : proposals.length === 0 ? (
              <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-lg border border-border/60 bg-card/40 text-muted-foreground p-4 text-center">
                <AlertCircleIcon className="h-8 w-8 text-muted-foreground/40" />
                <p>
                  {symbolSearch
                    ? `No proposals found matching symbol "${symbolSearch}".`
                    : statusFilter === "pending_approval"
                      ? selectedBatchGenerated
                        ? `This run reports ${selectedBatchGenerated} generated proposal(s), but none are pending. Check Expired or All Trades; proposals from D0 stop accepting approval at 09:00 IST on D1.`
                        : "No proposals are waiting for approval. Generate a batch from the latest scan to review candidates."
                      : statusFilter === "approved"
                        ? "No proposals have been approved by you for this run. Generated proposals remain Pending until you explicitly approve them."
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
                                <span className="h-1.5 w-1.5 rounded-full bg-green-500" title="Live Eligible" />
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
                          <td className="py-2.5 px-3 text-ko">
                            ₹{Number(p.initial_stop).toFixed(2)} (
                            {Number(p.stop_distance_pct).toFixed(2)}%)
                          </td>
                          <td className="py-2.5 px-3 text-green-400 font-semibold">
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
    </section>
  )
}
