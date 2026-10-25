import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  ActivityIcon,
  ClipboardCheckIcon,
  HistoryIcon,
  InboxIcon,
  PlayIcon,
  SearchIcon,
  SearchXIcon,
  XIcon,
} from "lucide-react"

import { StatusChip } from "@/components/terminal/bits"
import { EmptyState, Panel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Spinner } from "@/components/ui/spinner"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { defaultScanRunId, productionScanRuns, useScanRuns } from "@/features/screener/api"
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, fmtR, fmtRelative, fmtTime, toNum, toneCls } from "@/lib/format"
import { shortSymbol } from "@/lib/marketSymbols"
import { useDebounced } from "@/lib/useDebounced"
import { useUrlState } from "@/lib/useUrlState"
import { cn } from "@/lib/utils"
import {
  useCapacityConflicts,
  useEntrySupervisorStatus,
  useFormingPatterns,
  useProposalBatch,
  useProposalBatches,
  useProposalGenerationResults,
  useProposalHistory,
  useRejectedAttempts,
  useResolveCapacityConflict,
  useTradeProposals,
  useTriggerProposalBatch,
  type ProposalRunSummary,
  type TradeProposalItem,
} from "./api"
import { AttemptDetail } from "./AttemptDetail"
import { MarketContextDetails, MarketLightChip } from "./MarketContextPanel"
import { PaperLedger } from "./PaperLedger"
import { ProposalDetail } from "./ProposalDetail"
import {
  AttemptOutcomeChip,
  EntryStateChip,
  humanizeCode,
  PositionStateChip,
  ProposalStatusChip,
  TemplateBadge,
} from "./proposalUi"

type HubTab = "pending" | "inplay" | "history" | "rejected" | "forming" | "runs" | "ledger"

const VIEW_DEFAULTS = {
  tab: "pending",
  q: "",
  status: "all",
  from: "",
  to: "",
  astatus: "all",
  existing: "",
  fstatus: "watching",
  run: "",
  proposal: "",
  attempt: "",
}

function price(value: unknown): string {
  const n = toNum(value as number | string | null | undefined)
  return n === null ? "—" : fmtNum(n)
}

function isInPlay(proposal: TradeProposalItem): boolean {
  const positionOpen = proposal.position !== null && !["closed", "cancelled"].includes(proposal.position.state)
  return positionOpen || (proposal.entry_state !== "expired" && proposal.entry_state !== "filled")
}

/* ── shared table bits ── */

function ClickableRow({ onOpen, selected, children }: { onOpen: () => void; selected?: boolean; children: ReactNode }) {
  return (
    <tr
      className={cn("clickable", selected && "sel")}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpen()
        }
      }}
      tabIndex={0}
    >
      {children}
    </tr>
  )
}

function SymbolCell({ symbol, sub }: { symbol: string; sub?: ReactNode }) {
  return (
    <td className="l">
      <span className="font-sans text-xs font-semibold text-foreground">{shortSymbol(symbol)}</span>
      {sub && <span className="block font-sans text-2xs text-muted-text">{sub}</span>}
    </td>
  )
}

function TableState({
  isLoading,
  error,
  empty,
  emptyIcon = InboxIcon,
  emptyTitle,
  emptyText,
  children,
}: {
  isLoading: boolean
  error: unknown
  empty: boolean
  emptyIcon?: typeof InboxIcon
  emptyTitle: string
  emptyText?: ReactNode
  children: ReactNode
}) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-text">
        <Spinner /> Loading…
      </div>
    )
  }
  if (error) {
    return (
      <EmptyState icon={SearchXIcon} title="Could not load" tone="bad">
        {error instanceof Error ? error.message : "The API is unavailable."}
      </EmptyState>
    )
  }
  if (empty) {
    return (
      <EmptyState icon={emptyIcon} title={emptyTitle}>
        {emptyText}
      </EmptyState>
    )
  }
  return <>{children}</>
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return (
    <label className="fsearch">
      <SearchIcon aria-hidden="true" className="ic" />
      <input aria-label={placeholder} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} type="text" value={value} />
      {value && (
        <button aria-label="Clear search" className="wadd-clr" onClick={() => onChange("")} type="button">
          <XIcon className="size-3" />
        </button>
      )}
    </label>
  )
}

/* ── tabs ── */

function PendingTab({ onOpen, selectedId }: { onOpen: (id: string) => void; selectedId: string }) {
  const { data = [], isLoading, error } = useTradeProposals("pending_approval", { limit: 200 })
  const rows = useMemo(
    () => [...data].sort((a, b) => a.approval_deadline.localeCompare(b.approval_deadline)),
    [data],
  )
  return (
    <TableState
      empty={rows.length === 0}
      emptyIcon={ClipboardCheckIcon}
      emptyText="New proposals land here after the EOD batch. Decide before the 09:00 IST deadline on the next session."
      emptyTitle="Nothing waiting for your decision"
      error={error}
      isLoading={isLoading}
    >
      <table className="tbl">
        <thead>
          <tr>
            <th className="l">SYMBOL</th>
            <th className="l">TEMPLATE</th>
            <th>PIVOT</th>
            <th>STOP</th>
            <th>T1</th>
            <th>CONF</th>
            <th className="l">DEADLINE</th>
            <th className="l">ELIGIBILITY</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <ClickableRow key={row.id} onOpen={() => onOpen(row.id)} selected={row.id === selectedId}>
              <SymbolCell sub={`EOD ${fmtDate(row.as_of_date)}`} symbol={row.symbol} />
              <td className="l">
                <TemplateBadge template={row.entry_template} />
              </td>
              <td>{price(row.pivot_price)}</td>
              <td className="text-ko">{price(row.initial_stop)}</td>
              <td className="text-ok">
                {price(row.t1)}
                {row.geometry?.t1_r && <span className="ml-1 text-2xs text-muted-text">{fmtR(row.geometry.t1_r)}</span>}
              </td>
              <td>{fmtNum(toNum(row.confidence), 0)}</td>
              <td className="l">
                {fmtDateTime(row.approval_deadline)}
                <span className="ml-1.5 text-2xs text-wa">{fmtRelative(row.approval_deadline)}</span>
              </td>
              <td className="l">
                {row.live_eligible ? <StatusChip tone="fill">Live eligible</StatusChip> : <StatusChip tone="off">Review only</StatusChip>}
              </td>
            </ClickableRow>
          ))}
        </tbody>
      </table>
    </TableState>
  )
}

function InPlayTab({ onOpen, selectedId }: { onOpen: (id: string) => void; selectedId: string }) {
  const { data = [], isLoading, error } = useTradeProposals("approved", { limit: 200 })
  const rows = useMemo(() => data.filter(isInPlay), [data])
  return (
    <TableState
      empty={rows.length === 0}
      emptyText="Approved proposals stay here while their entry is armed or their position is open."
      emptyTitle="No approved proposals in play"
      error={error}
      isLoading={isLoading}
    >
      <table className="tbl">
        <thead>
          <tr>
            <th className="l">SYMBOL</th>
            <th className="l">APPROVED</th>
            <th className="l">ENTRY</th>
            <th className="l">POSITION</th>
            <th>REALIZED</th>
            <th>PIVOT</th>
            <th>STOP</th>
            <th>T1</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <ClickableRow key={row.id} onOpen={() => onOpen(row.id)} selected={row.id === selectedId}>
              <SymbolCell sub={<TemplateBadge template={row.entry_template} />} symbol={row.symbol} />
              <td className="l">
                {fmtDateTime(row.decided_at)}
                <span className="block text-2xs text-muted-text">{row.decision_source === "paper_auto" ? "auto (paper)" : "by you"}</span>
              </td>
              <td className="l">
                <EntryStateChip state={row.entry_state} />
              </td>
              <td className="l">
                <PositionStateChip position={row.position} />
              </td>
              <td className={toneCls(toNum(row.position?.realized_pnl))}>
                {row.position ? fmtMoney(row.position.realized_pnl, { sign: true }) : "—"}
              </td>
              <td>{price(row.pivot_price)}</td>
              <td className="text-ko">{price(row.initial_stop)}</td>
              <td className="text-ok">{price(row.t1)}</td>
            </ClickableRow>
          ))}
        </tbody>
      </table>
    </TableState>
  )
}

function HistoryTab({
  view,
  setView,
  onOpen,
}: {
  view: typeof VIEW_DEFAULTS
  setView: (patch: Partial<Record<keyof typeof VIEW_DEFAULTS, string | null>>) => void
  onOpen: (id: string) => void
}) {
  const [search, setSearch] = useState(view.q)
  const debounced = useDebounced(search, 300)
  useEffect(() => {
    if (debounced !== view.q) setView({ q: debounced })
  }, [debounced, setView, view.q])

  const history = useProposalHistory(view.status, {
    symbol: view.q || null,
    fromDate: view.from || null,
    toDate: view.to || null,
  })
  const rows = useMemo(() => history.data?.pages.flat() ?? [], [history.data])
  const filtered = Boolean(view.q || view.from || view.to || view.status !== "all")

  return (
    <div className="flex flex-col">
      <div className="sfilter !px-0 !pb-3">
        <SearchBox onChange={setSearch} placeholder="Symbol contains…" value={search} />
        <select aria-label="Status" className="filter-select" onChange={(event) => setView({ status: event.target.value })} value={view.status}>
          <option value="all">Status: any</option>
          <option value="pending_approval">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="expired_unapproved">Expired</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs text-muted-text">
          From
          <input aria-label="From EOD date" className="filter-select !pr-2" onChange={(event) => setView({ from: event.target.value })} type="date" value={view.from} />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted-text">
          To
          <input aria-label="To EOD date" className="filter-select !pr-2" onChange={(event) => setView({ to: event.target.value })} type="date" value={view.to} />
        </label>
        {filtered && (
          <button
            className="sf-clear"
            onClick={() => {
              setSearch("")
              setView({ q: null, status: null, from: null, to: null })
            }}
            type="button"
          >
            Clear filters
          </button>
        )}
        <span className="sfmeta">
          <b>{rows.length}</b>
          {history.hasNextPage ? "+" : ""} proposals
        </span>
      </div>
      <TableState
        empty={rows.length === 0}
        emptyIcon={HistoryIcon}
        emptyText={filtered ? "Try widening the date range or clearing the filters." : "Proposals appear here once the first batch runs."}
        emptyTitle="No proposals found"
        error={history.error}
        isLoading={history.isLoading}
      >
        <table className="tbl">
          <thead>
            <tr>
              <th className="l">EOD</th>
              <th className="l">SYMBOL</th>
              <th className="l">TEMPLATE</th>
              <th>PIVOT</th>
              <th>STOP</th>
              <th>T1</th>
              <th>CONF</th>
              <th className="l">STATUS</th>
              <th className="l">ENTRY</th>
              <th className="l">DECISION</th>
              <th>P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <ClickableRow key={row.id} onOpen={() => onOpen(row.id)} selected={row.id === view.proposal}>
                <td className="l">{fmtDate(row.as_of_date)}</td>
                <SymbolCell symbol={row.symbol} />
                <td className="l">
                  <TemplateBadge template={row.entry_template} />
                </td>
                <td>{price(row.pivot_price)}</td>
                <td className="text-ko">{price(row.initial_stop)}</td>
                <td className="text-ok">{price(row.t1)}</td>
                <td>{fmtNum(toNum(row.confidence), 0)}</td>
                <td className="l">
                  <ProposalStatusChip status={row.status} />
                </td>
                <td className="l">{row.status === "approved" ? <EntryStateChip state={row.entry_state} /> : <span className="text-muted-text">—</span>}</td>
                <td className="l text-2xs">
                  {row.decided_at ? (
                    <>
                      {fmtDateTime(row.decided_at)}
                      <span className="block text-muted-text">{row.decision_source === "paper_auto" ? "auto (paper)" : "you"}</span>
                    </>
                  ) : (
                    <span className="text-muted-text">—</span>
                  )}
                </td>
                <td className={toneCls(toNum(row.position?.realized_pnl))}>
                  {row.position ? fmtMoney(row.position.realized_pnl, { sign: true }) : "—"}
                </td>
              </ClickableRow>
            ))}
          </tbody>
        </table>
        {history.hasNextPage && (
          <div className="flex justify-center py-3">
            <Button disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()} size="sm" type="button" variant="outline">
              {history.isFetchingNextPage ? <Spinner data-icon="inline-start" /> : null}
              Load older proposals
            </Button>
          </div>
        )}
      </TableState>
    </div>
  )
}

function RejectedTab({
  view,
  setView,
  onOpenAttempt,
}: {
  view: typeof VIEW_DEFAULTS
  setView: (patch: Partial<Record<keyof typeof VIEW_DEFAULTS, string | null>>) => void
  onOpenAttempt: (id: string) => void
}) {
  const [search, setSearch] = useState(view.q)
  const debounced = useDebounced(search, 300)
  useEffect(() => {
    if (debounced !== view.q) setView({ q: debounced })
  }, [debounced, setView, view.q])

  const { data = [], isLoading, error } = useRejectedAttempts({
    status: view.astatus,
    symbol: view.q || null,
    includeExisting: view.existing === "1",
    limit: 200,
  })

  return (
    <div className="flex flex-col">
      <div className="sfilter !px-0 !pb-3">
        <SearchBox onChange={setSearch} placeholder="Symbol contains…" value={search} />
        <select aria-label="Outcome" className="filter-select" onChange={(event) => setView({ astatus: event.target.value })} value={view.astatus}>
          <option value="all">Outcome: any</option>
          <option value="invalid">Rejected by rules</option>
          <option value="uncertain">Uncertain</option>
          <option value="partial">Partial / forming</option>
          <option value="failed">Failed</option>
          <option value="timed_out">Timed out</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs text-muted-text">
          <input
            checked={view.existing === "1"}
            onChange={(event) => setView({ existing: event.target.checked ? "1" : null })}
            type="checkbox"
          />
          Include “already proposed”
        </label>
        <span className="sfmeta">
          <b>{data.length}</b> candidates · latest attempt per candidate
        </span>
      </div>
      <TableState
        empty={data.length === 0}
        emptyIcon={SearchXIcon}
        emptyText="Shortlisted stocks that the structural gates, Gemini audit or numeric gates did not turn into a proposal."
        emptyTitle="No unproposed candidates"
        error={error}
        isLoading={isLoading}
      >
        <table className="tbl">
          <thead>
            <tr>
              <th className="l">EOD</th>
              <th className="l">SYMBOL</th>
              <th className="l">OUTCOME</th>
              <th className="l">REASON</th>
              <th className="l">GEMINI</th>
              <th className="l">WHEN</th>
            </tr>
          </thead>
          <tbody>
            {data.map((attempt) => {
              const classification = attempt.structured_output?.classification ?? attempt.structured_output?.verdict
              return (
                <ClickableRow key={attempt.id} onOpen={() => onOpenAttempt(attempt.id)} selected={attempt.id === view.attempt}>
                  <td className="l">{fmtDate(attempt.as_of_date)}</td>
                  <SymbolCell symbol={attempt.symbol} />
                  <td className="l">
                    <AttemptOutcomeChip attempt={attempt} />
                  </td>
                  <td className="l max-w-[360px] truncate font-sans" title={attempt.error_message ?? undefined}>
                    {humanizeCode(attempt.error_type)}
                  </td>
                  <td className="l capitalize">{classification ? String(classification).replaceAll("_", " ") : "—"}</td>
                  <td className="l">{fmtDateTime(attempt.started_at)}</td>
                </ClickableRow>
              )
            })}
          </tbody>
        </table>
      </TableState>
    </div>
  )
}

function FormingTab({ view, setView }: { view: typeof VIEW_DEFAULTS; setView: (patch: Partial<Record<keyof typeof VIEW_DEFAULTS, string | null>>) => void }) {
  const { openChart } = useTradingAppContext()
  const { data = [], isLoading, error } = useFormingPatterns(view.fstatus)
  return (
    <div className="flex flex-col">
      <div className="sfilter !px-0 !pb-3">
        <select aria-label="Watch status" className="filter-select" onChange={(event) => setView({ fstatus: event.target.value })} value={view.fstatus}>
          <option value="watching">Watching</option>
          <option value="promoted">Promoted to proposal</option>
          <option value="broken_down">Broken down</option>
          <option value="expired">Expired</option>
          <option value="all">All</option>
        </select>
        <span className="sfmeta">
          <b>{data.length}</b> bases · rechecked on each EOD batch · click a row for its chart
        </span>
      </div>
      <TableState
        empty={data.length === 0}
        emptyText="Bases that look like VCPs but are not mature yet are watched here for up to 10 sessions."
        emptyTitle="No forming bases"
        error={error}
        isLoading={isLoading}
      >
        <table className="tbl">
          <thead>
            <tr>
              <th className="l">SYMBOL</th>
              <th className="l">STATE</th>
              <th className="l">FIRST SEEN</th>
              <th className="l">LAST EOD</th>
              <th className="l">NEXT CHECK</th>
              <th className="l">STATUS</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <ClickableRow key={row.id} onOpen={() => openChart(row.symbol)}>
                <SymbolCell symbol={row.symbol} />
                <td className="l capitalize">{row.forming_state.replaceAll("_", " ")}</td>
                <td className="l">{fmtDate(row.first_seen_as_of)}</td>
                <td className="l">{fmtDate(row.last_as_of)}</td>
                <td className="l">{row.status === "watching" ? fmtDate(row.next_check_date) : "—"}</td>
                <td className="l">
                  <StatusChip tone={row.status === "watching" ? "work" : row.status === "promoted" ? "fill" : row.status === "broken_down" ? "rej" : "off"}>
                    {row.status.replaceAll("_", " ")}
                  </StatusChip>
                </td>
              </ClickableRow>
            ))}
          </tbody>
        </table>
      </TableState>
    </div>
  )
}

function runLabel(run: ProposalRunSummary): string {
  return run.run_type === "single" ? `Single · ${shortSymbol(run.single_symbol ?? "—")}` : `Batch · ${run.candidates_total} charts`
}

function RunLedger({
  runId,
  onOpenProposal,
  onOpenAttempt,
}: {
  runId: string
  onOpenProposal: (id: string) => void
  onOpenAttempt: (id: string) => void
}) {
  const { data, isLoading, error } = useProposalGenerationResults(runId)
  const proposals = useTradeProposals("all", { automationRunId: runId, limit: 200, refetchInterval: 15_000 })
  const proposalBySymbol = useMemo(() => new Map((proposals.data ?? []).map((proposal) => [proposal.symbol, proposal.id])), [proposals.data])
  const existingCount = data?.results.filter((attempt) => attempt.error_type === "proposal_already_exists").length ?? 0

  return (
    <Panel
      actions={
        data && (
          <span className="font-mono text-2xs text-muted-text">
            {data.candidates_processed}/{data.candidates_total} charts · <b className="text-ok">{data.proposals_generated}</b> proposed ·{" "}
            <b className="text-ko">{data.proposals_rejected}</b> rejected · {data.proposals_uncertain} uncertain · {data.proposals_failed} failed
            {existingCount > 0 ? ` · ${existingCount} already proposed` : ""}
          </span>
        )
      }
      flush
      title={data ? `Run ledger · ${data.status}` : "Run ledger"}
    >
      {data?.error_message && <p className="border-b border-border-soft bg-ko-soft px-4 py-2 text-xs text-ko">{data.error_message}</p>}
      <TableState
        empty={(data?.results.length ?? 0) === 0}
        emptyText={data?.status === "running" ? "Attempts appear as the worker processes each chart." : "No audited attempts were recorded for this run."}
        emptyTitle="No attempts"
        error={error}
        isLoading={isLoading}
      >
        <table className="tbl">
          <thead>
            <tr>
              <th className="l">SYMBOL</th>
              <th className="l">OUTCOME</th>
              <th className="l">GEMINI</th>
              <th className="l">REASON</th>
              <th>ATTEMPT</th>
            </tr>
          </thead>
          <tbody>
            {(data?.results ?? []).map((attempt) => {
              const proposalId = attempt.status === "valid" ? proposalBySymbol.get(attempt.symbol) : undefined
              const existingId = attempt.error_details?.existing_proposal_id
              const classification = attempt.structured_output?.classification ?? attempt.structured_output?.verdict
              return (
                <ClickableRow
                  key={attempt.id}
                  onOpen={() => {
                    if (proposalId) onOpenProposal(proposalId)
                    else if (typeof existingId === "string") onOpenProposal(existingId)
                    else onOpenAttempt(attempt.id)
                  }}
                >
                  <SymbolCell symbol={attempt.symbol} />
                  <td className="l">
                    <AttemptOutcomeChip attempt={attempt} />
                  </td>
                  <td className="l capitalize">{classification ? String(classification).replaceAll("_", " ") : "—"}</td>
                  <td className="l max-w-[320px] truncate font-sans" title={attempt.error_message ?? undefined}>
                    {attempt.error_type ? humanizeCode(attempt.error_type) : proposalId ? "Opens the proposal" : "—"}
                  </td>
                  <td>#{attempt.attempt_number}</td>
                </ClickableRow>
              )
            })}
          </tbody>
        </table>
      </TableState>
    </Panel>
  )
}

function RunsTab({
  view,
  setView,
  onOpenProposal,
  onOpenAttempt,
}: {
  view: typeof VIEW_DEFAULTS
  setView: (patch: Partial<Record<keyof typeof VIEW_DEFAULTS, string | null>>) => void
  onOpenProposal: (id: string) => void
  onOpenAttempt: (id: string) => void
}) {
  const [limit, setLimit] = useState(30)
  const { data: runs = [], isLoading, error } = useProposalBatches(limit)
  const selected = view.run || runs[0]?.id || ""

  return (
    <div className="grid min-h-0 gap-4 lg:grid-cols-[300px_1fr]">
      <Panel flush title="Generation runs">
        <TableState empty={runs.length === 0} emptyText="Run Generate batch after an EOD scan." emptyTitle="No runs yet" error={error} isLoading={isLoading}>
          <ul className="max-h-[calc(100vh-320px)] overflow-y-auto">
            {runs.map((run) => (
              <li key={run.id}>
                <button
                  className={cn(
                    "flex w-full flex-col gap-0.5 border-b border-border-soft px-4 py-2 text-left hover:bg-hl",
                    run.id === selected && "bg-accent-soft",
                  )}
                  onClick={() => setView({ run: run.id })}
                  type="button"
                >
                  <span className="flex items-center justify-between gap-2 text-xs font-semibold text-foreground">
                    {runLabel(run)}
                    <StatusChip tone={run.status === "completed" ? "fill" : run.status === "running" ? "work" : run.status === "idle" ? "off" : "rej"}>
                      {run.status.replaceAll("_", " ")}
                    </StatusChip>
                  </span>
                  <span className="font-mono text-2xs text-muted-text">
                    {run.as_of_date ? `EOD ${fmtDate(run.as_of_date)} · ` : ""}
                    {fmtDateTime(run.created_at)} · {run.proposals_generated} proposed
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {runs.length >= limit && limit < 100 && (
            <div className="flex justify-center py-2">
              <Button onClick={() => setLimit((value) => Math.min(100, value + 30))} size="sm" type="button" variant="ghost">
                Load older runs
              </Button>
            </div>
          )}
        </TableState>
      </Panel>
      {selected ? (
        <RunLedger onOpenAttempt={onOpenAttempt} onOpenProposal={onOpenProposal} runId={selected} />
      ) : (
        <EmptyState icon={ActivityIcon} title="Pick a run" />
      )}
    </div>
  )
}

/* ── header strip ── */

function BatchControls() {
  const queryClient = useQueryClient()
  const scanRuns = useScanRuns()
  const latestScanId = defaultScanRunId(productionScanRuns(scanRuns.data))
  const latestScan = productionScanRuns(scanRuns.data).find((run) => run.id === latestScanId)
  const batch = useProposalBatch(latestScanId)
  const trigger = useTriggerProposalBatch()
  const running = batch.data?.status === "running" || trigger.isPending

  // Refresh lists once a batch finishes.
  const lastStatus = useRef<string | null>(null)
  useEffect(() => {
    const status = batch.data?.status ?? null
    if (lastStatus.current === "running" && status !== "running") {
      void queryClient.invalidateQueries({ queryKey: ["automation"] })
    }
    lastStatus.current = status
  }, [batch.data?.status, queryClient])

  const message = trigger.error instanceof Error
    ? trigger.error.message
    : batch.data?.status === "running"
      ? `Generating ${batch.data.candidates_processed}/${batch.data.candidates_total} · ${batch.data.proposals_generated} proposed`
      : batch.data?.status === "completed"
        ? `Last batch: ${batch.data.proposals_generated} proposed from ${batch.data.candidates_processed} charts`
        : batch.data?.status === "timed_out" || batch.data?.status === "failed"
          ? (batch.data.error_message ?? `Last batch ${batch.data.status.replaceAll("_", " ")}`)
          : latestScan
            ? `Scan ${fmtDate(latestScan.as_of_date)} · ${latestScan.passing_count} setups`
            : "No completed scan yet"
  const failed = Boolean(trigger.error) || batch.data?.status === "timed_out" || batch.data?.status === "failed"

  return (
    <div className="flex items-center gap-2">
      <span className={cn("hidden max-w-80 truncate font-mono text-2xs xl:inline", failed ? "text-ko" : "text-muted-text")} title={message}>
        {message}
      </span>
      <Button
        disabled={running || !latestScan || latestScan.status !== "succeeded"}
        onClick={() => trigger.mutate(latestScanId)}
        size="sm"
        title="Generate proposals for the latest scan's shortlist"
        type="button"
      >
        {running ? <Spinner data-icon="inline-start" /> : <PlayIcon data-icon="inline-start" className="fill-current" />}
        {running ? "Generating" : "Generate batch"}
      </Button>
    </div>
  )
}

function SupervisorChip() {
  const { data } = useEntrySupervisorStatus()
  const active = data?.status === "active"
  return (
    <span
      className={cn("chip", active ? "text-ok" : "text-ko")}
      title={`Entry supervisor ${data?.status ?? "unknown"} · market data ${data?.market_data.status ?? "unknown"}`}
    >
      <i className="dot" />
      SUPERVISOR {active ? "ON" : "OFF"}
      <span className="font-mono text-2xs text-fg2">
        {data?.armed_legs_count ?? 0} armed · {data?.waiting_for_reset_count ?? 0} reset
      </span>
    </span>
  )
}

function AttentionStrip() {
  const { data: conflicts = [] } = useCapacityConflicts()
  const resolve = useResolveCapacityConflict()
  const { data: supervisor } = useEntrySupervisorStatus()
  const todayKey = fmtDate(new Date())
  const gateEvents = (supervisor?.recent_allocation_events ?? [])
    .filter((event) => event.context_gate_reasons?.length && fmtDate(event.created_at) === todayKey)
    .slice(0, 3)
  if (conflicts.length === 0 && gateEvents.length === 0) return null

  return (
    <div className="flex flex-none flex-col gap-2 px-5 pb-3">
      {conflicts.map((conflict) => (
        <div className="rounded-md border border-wa/40 bg-wa-soft p-3" key={conflict.id}>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <b className="text-sm text-wa">Capacity tie — pick one or skip all</b>
            <span className="text-xs text-muted-text">
              Signal bar {fmtTime(conflict.bar_timestamp)} IST ({fmtRelative(conflict.bar_timestamp)}). Unresolved ties are skipped when the signal
              expires.
            </span>
            <Button
              className="ml-auto"
              disabled={resolve.isPending}
              onClick={() => resolve.mutate({ id: conflict.id, chosenLegId: null })}
              size="sm"
              type="button"
              variant="outline"
            >
              Skip all
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {conflict.candidates.map((candidate) => (
              <Button
                disabled={resolve.isPending}
                key={candidate.leg_id}
                onClick={() => resolve.mutate({ id: conflict.id, chosenLegId: candidate.leg_id })}
                size="sm"
                type="button"
              >
                {shortSymbol(candidate.symbol)} L{candidate.leg_index} · {fmtR(candidate.conservative_rr)}
              </Button>
            ))}
          </div>
        </div>
      ))}
      {gateEvents.map((event) => (
        <p className="rounded-md border border-wa/30 bg-wa-soft px-3 py-1.5 text-xs text-wa" key={event.id}>
          {fmtTime(event.created_at)} · allocation {event.event_type.replaceAll("_", " ")} · {event.market_context_mode ?? "breaker"} ·{" "}
          {event.context_gate_reasons.join(", ")}
          {event.context_adjusted_risk_ceiling !== null && ` · risk ceiling ${fmtMoney(event.context_adjusted_risk_ceiling)}`}
        </p>
      ))}
    </div>
  )
}

/* ── page ── */

/** Proposals: pending decisions, live entries, full history, runs and the paper ledger. */
export function ProposalsHub() {
  const [view, setView] = useUrlState(VIEW_DEFAULTS)
  const [contextOpen, setContextOpen] = useState(false)
  const openedHere = useRef(false)
  const tab = view.tab as HubTab

  const pending = useTradeProposals("pending_approval", { limit: 200 })
  const approved = useTradeProposals("approved", { limit: 200 })
  const forming = useFormingPatterns("watching")
  const inPlayCount = (approved.data ?? []).filter(isInPlay).length

  const openProposal = useCallback(
    (id: string) => {
      openedHere.current = true
      setView({ proposal: id, attempt: null }, { replace: Boolean(view.proposal || view.attempt) })
    },
    [setView, view.attempt, view.proposal],
  )
  const openAttempt = useCallback(
    (id: string) => {
      openedHere.current = true
      setView({ attempt: id, proposal: null }, { replace: Boolean(view.proposal || view.attempt) })
    },
    [setView, view.attempt, view.proposal],
  )
  const closeDrawer = useCallback(() => {
    if (openedHere.current) {
      openedHere.current = false
      window.history.back()
    } else {
      setView({ proposal: null, attempt: null })
    }
  }, [setView])
  const openRun = useCallback(
    (runId: string) => {
      openedHere.current = false
      setView({ tab: "runs", run: runId, proposal: null, attempt: null })
    },
    [setView],
  )

  const tabs: { key: HubTab; label: string; count?: number; attention?: boolean }[] = [
    { key: "pending", label: "Pending", count: pending.data?.length, attention: true },
    { key: "inplay", label: "In play", count: inPlayCount },
    { key: "history", label: "History" },
    { key: "rejected", label: "Not proposed" },
    { key: "forming", label: "Forming", count: forming.data?.length },
    { key: "runs", label: "Runs" },
    { key: "ledger", label: "Paper ledger" },
  ]
  const drawerOpen = Boolean(view.proposal || view.attempt)

  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>Proposals</h2>
          <p className="vmeta">Decide before 09:00 IST on the next session · levels and sizing are fixed by Python</p>
        </div>
        <div className="vhead-right flex-wrap justify-end">
          <MarketLightChip onClick={() => setContextOpen(true)} />
          <SupervisorChip />
          <BatchControls />
        </div>
      </div>

      <AttentionStrip />

      <nav aria-label="Proposal views" className="ptabs" role="tablist">
        {tabs.map((item) => (
          <button
            aria-selected={tab === item.key}
            className={cn("ptab", tab === item.key && "on")}
            key={item.key}
            onClick={() => setView({ tab: item.key, q: null, run: null })}
            role="tab"
            type="button"
          >
            {item.label}
            {item.count !== undefined && <span className={cn("tcount", item.attention && item.count > 0 && "attn")}>{item.count}</span>}
          </button>
        ))}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {tab === "pending" && <PendingTab onOpen={openProposal} selectedId={view.proposal} />}
        {tab === "inplay" && <InPlayTab onOpen={openProposal} selectedId={view.proposal} />}
        {tab === "history" && <HistoryTab onOpen={openProposal} setView={setView} view={view} />}
        {tab === "rejected" && <RejectedTab onOpenAttempt={openAttempt} setView={setView} view={view} />}
        {tab === "forming" && <FormingTab setView={setView} view={view} />}
        {tab === "runs" && <RunsTab onOpenAttempt={openAttempt} onOpenProposal={openProposal} setView={setView} view={view} />}
        {tab === "ledger" && <PaperLedger />}
      </div>

      <Sheet onOpenChange={(open) => !open && closeDrawer()} open={drawerOpen}>
        <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-[min(920px,92vw)]" side="right">
          <SheetHeader className="sr-only">
            <SheetTitle>{view.proposal ? "Proposal detail" : "Attempt detail"}</SheetTitle>
            <SheetDescription>Immutable proposal record</SheetDescription>
          </SheetHeader>
          <div className="px-5 pt-10 pb-6">
            {view.proposal && <ProposalDetail onDecided={closeDrawer} onOpenRun={openRun} proposalId={view.proposal} />}
            {view.attempt && <AttemptDetail attemptId={view.attempt} onOpenProposal={openProposal} onOpenRun={openRun} />}
          </div>
        </SheetContent>
      </Sheet>

      <Sheet onOpenChange={setContextOpen} open={contextOpen}>
        <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-[560px]" side="right">
          <SheetHeader>
            <SheetTitle>Market context (P9)</SheetTitle>
            <SheetDescription>Deterministic EOD light and sector gates. Enforcement and breaker resets live in Operations.</SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-6">
            <MarketContextDetails />
          </div>
        </SheetContent>
      </Sheet>
    </section>
  )
}
