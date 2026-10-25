import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  FlaskConical,
  LineChart,
  Play,
  Search,
  SearchX,
  Sparkles,
  StarIcon,
  XCircle,
} from "lucide-react"

import { GradeChip, StatusChip, type StatusTone } from "@/components/terminal/bits"
import { EmptyState } from "@/components/terminal/panel"
import { useToast } from "@/components/terminal/toast"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useAuthStatus } from "@/features/auth/api"
import { useTradingAppContext } from "@/features/dashboard/app-context"
import { useTriggerSingleProposal } from "@/features/proposals/api"
import type { ScanResult } from "@/features/screener/api"
import {
  applyScanListFilters,
  formatScanRun,
  resolveSort,
  SCAN_GRADES,
  SCAN_LIST_DEFAULTS,
  SCAN_SORT_DEFAULT_DIR,
  useScanRunResults,
  type ScanSortKey,
} from "@/features/screener/scanList"
import { useScanWorkflow } from "@/features/screener/useScanWorkflow"
import { VcpVisionSheet, VcpVisionStatusBadge } from "@/features/screener/VcpVisionSheet"
import { fmtDate, fmtNum } from "@/lib/format"
import { useUrlState } from "@/lib/useUrlState"
import { cn } from "@/lib/utils"

const PAGE_SIZES = ["10", "25", "50", "100", "0"] as const

const TREND_CHECKS: [string, string][] = [
  ["price_above_50_sma", "Close above SMA 50"],
  ["price_above_150_200_sma", "Close above SMA 150 & 200"],
  ["sma_50_above_150_200_sma", "SMA 50 above SMA 150 & 200"],
  ["sma_150_above_200_sma", "SMA 150 above SMA 200"],
  ["sma_200_trending_up_1m", "SMA 200 rising over 1 month"],
]

function SortHeader({
  label,
  sortKey,
  active,
  dir,
  onSort,
  className,
}: {
  label: string
  sortKey: ScanSortKey
  active: boolean
  dir: "asc" | "desc"
  onSort: (key: ScanSortKey) => void
  className?: string
}) {
  return (
    <th
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn("srt", active && "on", className)}
      onClick={() => onSort(sortKey)}
    >
      {label}
      <span className="arr">{active ? (dir === "asc" ? "▲" : "▼") : "↕"}</span>
    </th>
  )
}

function TrendCell({ row }: { row: ScanResult }) {
  const checks = TREND_CHECKS.filter(([key]) => key in row.core_checks)
  const passed = checks.filter(([key]) => row.core_checks[key]).length
  if (checks.length === 0) return <span className="text-muted-text">—</span>
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex cursor-default items-center gap-1" />}>
        {checks.map(([key]) => (
          <span
            aria-hidden="true"
            className={cn("size-1.5 rounded-full", row.core_checks[key] ? "bg-ok" : "bg-ko")}
            key={key}
          />
        ))}
        <span className={cn("ml-1", passed === checks.length ? "text-ok" : "text-fg2")}>
          {passed}/{checks.length}
        </span>
      </TooltipTrigger>
      <TooltipContent className="flex-col items-start gap-0.5 font-mono" side="bottom">
        {checks.map(([key, label]) => (
          <span key={key}>
            {row.core_checks[key] ? "✓" : "✗"} {label}
          </span>
        ))}
        <span className="mt-1 text-muted-text">
          SMA 50 {fmtNum(row.sma_50)} · 150 {fmtNum(row.sma_150)} · 200 {fmtNum(row.sma_200)}
        </span>
      </TooltipContent>
    </Tooltip>
  )
}

function RowAction({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            aria-label={label}
            className="ibtn"
            disabled={disabled}
            onClick={(event) => {
              event.stopPropagation()
              onClick()
            }}
            type="button"
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="left">{label}</TooltipContent>
    </Tooltip>
  )
}

export function ScannerPage() {
  const { openChart } = useTradingAppContext()
  const { toast } = useToast()
  const authStatus = useAuthStatus()
  const scanWorkflow = useScanWorkflow(authStatus.data)
  const generateProposal = useTriggerSingleProposal()

  const [view, setView] = useUrlState({ ...SCAN_LIST_DEFAULTS, page: "1", size: "25", sel: "" })
  const [visionResult, setVisionResult] = useState<ScanResult | null>(null)
  const tableRef = useRef<HTMLTableSectionElement>(null)

  const { scanRuns, runs, runId, run: activeRun, resultsQuery } = useScanRunResults(view.run || null)
  const sort = resolveSort(view.sort, view.dir)

  // A scan started (or re-attached) from here becomes the visible run. The ref
  // keeps a remount from overriding the run chosen in the URL.
  const seenScanRunId = useRef(scanWorkflow.scanRunId)
  useEffect(() => {
    if (!scanWorkflow.scanRunId || scanWorkflow.scanRunId === seenScanRunId.current) return
    seenScanRunId.current = scanWorkflow.scanRunId
    setView({ run: scanWorkflow.scanRunId, page: null, sel: null })
  }, [scanWorkflow.scanRunId, setView])

  const filtered = useMemo(
    () => applyScanListFilters(resultsQuery.data ?? [], view),
    [resultsQuery.data, view],
  )

  const pageSize = Number(view.size) || 0
  const totalItems = filtered.length
  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(totalItems / pageSize))
  const pageIndex = Math.min(Math.max(1, Number(view.page) || 1), totalPages)
  const displayed = useMemo(
    () => (pageSize === 0 ? filtered : filtered.slice((pageIndex - 1) * pageSize, pageIndex * pageSize)),
    [filtered, pageIndex, pageSize],
  )

  const setFilter = (patch: Parameters<typeof setView>[0]) => setView({ ...patch, page: null })

  const onSort = (key: ScanSortKey) => {
    const dir = key === sort.key ? (sort.dir === "asc" ? "desc" : "asc") : SCAN_SORT_DEFAULT_DIR[key]
    setFilter({ sort: key, dir: dir === SCAN_SORT_DEFAULT_DIR[key] ? null : dir })
  }

  const openRowChart = (row: ScanResult) =>
    openChart(row.fyers_symbol, {
      list: "scan",
      run: runId ?? undefined,
      q: view.q,
      grade: view.grade === "ALL" ? undefined : view.grade,
      sort: view.sort === "rank" ? undefined : view.sort,
      dir: view.dir,
    })

  const triggerProposal = (row: ScanResult) =>
    generateProposal.mutate(row.id, {
      onSuccess: (result) => toast("ok", { title: `Proposal queued · ${row.symbol}`, text: result.message }),
      onError: (error) =>
        toast("bad", {
          title: `Could not queue proposal · ${row.symbol}`,
          text: error instanceof Error ? error.message : undefined,
        }),
    })

  const selectRow = (row: ScanResult) => setView({ sel: row.id })

  // ↑/↓ moves the selection, Enter opens the chart.
  const onTableKeyDown = (event: KeyboardEvent<HTMLTableSectionElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return
    event.preventDefault()
    const current = displayed.findIndex((row) => row.id === view.sel)
    const nextIndex = event.key === "ArrowDown" ? Math.min(current + 1, displayed.length - 1) : Math.max(current - 1, 0)
    const next = displayed[nextIndex]
    if (!next) return
    selectRow(next)
    tableRef.current?.querySelector<HTMLElement>(`[data-row-id="${next.id}"]`)?.focus()
  }

  const runTone: StatusTone = !activeRun
    ? "off"
    : activeRun.status === "succeeded"
      ? "fill"
      : activeRun.status === "failed"
        ? "rej"
        : activeRun.status === "cancelled"
          ? "off"
          : "work"
  const runStateLabel = activeRun ? activeRun.status.toUpperCase() : runs.length > 0 ? "IDLE" : "NO RUNS"
  const scanButtonLabel =
    scanWorkflow.phase === "syncing"
      ? "Syncing EOD"
      : scanWorkflow.phase === "attaching_scan"
        ? "Opening scan"
        : scanWorkflow.phase === "queued"
          ? "Waiting for worker"
          : scanWorkflow.phase === "scanning"
            ? "Scoring Nifty 500"
            : "Run EOD scan"

  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>
            Scanner <span className="sub">VCP setups ranked by technical score</span>
          </h2>
          <p className="vmeta">
            {activeRun ? (
              <>
                EOD <b>{fmtDate(activeRun.as_of_date ?? activeRun.created_at)}</b> · <b>{activeRun.passing_count}</b>{" "}
                eligible setups
              </>
            ) : scanRuns.data?.length ? (
              "No completed run yet — run the EOD scan."
            ) : (
              "Connect the backend to load scanner runs."
            )}
          </p>
        </div>
        <div className="vhead-right">
          <StatusChip tone={runTone}>{runStateLabel}</StatusChip>
          <select
            aria-label="Scanner run"
            className="filter-select min-w-[260px]"
            disabled={runs.length === 0}
            onChange={(event) => setView({ run: event.target.value, page: null, sel: null })}
            value={runId ?? ""}
          >
            {runs.length === 0 && <option value="">No scanner runs found</option>}
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                {formatScanRun(run)}
              </option>
            ))}
          </select>
          <button className="btn btn-primary" disabled={scanWorkflow.isBusy} onClick={() => void scanWorkflow.start()} type="button">
            {scanWorkflow.isBusy ? <Spinner data-icon="inline-start" /> : <Play aria-hidden="true" className="btn-ic fill-current" />}
            {scanButtonLabel}
          </button>
        </div>
      </div>

      <div className="sfilter">
        <label className="fsearch">
          <Search aria-hidden="true" className="ic" />
          <input
            aria-label="Search symbol or name"
            onChange={(event) => setFilter({ q: event.target.value })}
            placeholder="Search symbol or name"
            type="text"
            value={view.q}
          />
        </label>
        <select
          aria-label="Filter by grade"
          className="filter-select"
          onChange={(event) => setFilter({ grade: event.target.value })}
          value={view.grade}
        >
          {SCAN_GRADES.map((grade) => (
            <option key={grade} value={grade}>
              {grade === "ALL" ? "Grade: any" : `Grade: ${grade}`}
            </option>
          ))}
        </select>
        <select
          aria-label="Rows per page"
          className="filter-select"
          onChange={(event) => setFilter({ size: event.target.value })}
          value={view.size}
        >
          {PAGE_SIZES.map((size) => (
            <option key={size} value={size}>
              Rows: {size === "0" ? "all" : size}
            </option>
          ))}
        </select>
        {(view.q || view.grade !== "ALL") && (
          <button className="sf-clear" onClick={() => setFilter({ q: null, grade: null })} type="button">
            Clear filters
          </button>
        )}
        <span className="sfmeta">
          <b>{totalItems}</b> of {activeRun?.passing_count ?? totalItems} · double-click or Enter opens the chart
        </span>
      </div>

      <div className="tscroll">
        {resultsQuery.isLoading ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-text">
            <Spinner />
            Loading technical setups…
          </div>
        ) : resultsQuery.isError ? (
          <EmptyState
            action={
              <Button onClick={() => void resultsQuery.refetch()} size="sm" type="button" variant="outline">
                Retry
              </Button>
            }
            className="h-full"
            icon={XCircle}
            title="Could not load scanner results"
            tone="bad"
          >
            {resultsQuery.error instanceof Error ? resultsQuery.error.message : "The scanner results API is unavailable."}
          </EmptyState>
        ) : activeRun?.status === "failed" ? (
          <EmptyState className="h-full" icon={XCircle} title="Scanner run failed" tone="bad">
            {activeRun.error_message ?? "The worker did not provide an error message."}
          </EmptyState>
        ) : activeRun?.status === "queued" || activeRun?.status === "running" ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-text">
            <Spinner />
            {activeRun.status === "queued"
              ? "Personal scan is waiting for the scanner worker…"
              : "Technical scoring is running across the Nifty 500…"}
          </div>
        ) : displayed.length === 0 ? (
          <EmptyState className="h-full" icon={SearchX} title="No matching setups">
            {view.q || view.grade !== "ALL"
              ? "Try clearing the search or grade filter."
              : "Run an EOD scan to generate setups across the Nifty 500."}
          </EmptyState>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <SortHeader active={sort.key === "rank"} className="l" dir={sort.dir} label="RANK" onSort={onSort} sortKey="rank" />
                <th className="l">SYMBOL</th>
                <SortHeader active={sort.key === "score"} dir={sort.dir} label="SCORE" onSort={onSort} sortKey="score" />
                <th>CLOSE</th>
                <th className="l">TREND</th>
                <SortHeader active={sort.key === "off52w"} dir={sort.dir} label="OFF HIGH" onSort={onSort} sortKey="off52w" />
                <SortHeader active={sort.key === "rs"} dir={sort.dir} label="RS" onSort={onSort} sortKey="rs" />
                <SortHeader active={sort.key === "adtv"} dir={sort.dir} label="ADTV ₹CR" onSort={onSort} sortKey="adtv" />
                <th className="l">SHORTLIST</th>
                <th className="l">VISION</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody onKeyDown={onTableKeyDown} ref={tableRef}>
              {displayed.map((row) => {
                const selected = view.sel === row.id
                const proposalPending = generateProposal.isPending && generateProposal.variables === row.id
                return (
                  <tr
                    aria-selected={selected}
                    className={cn("cursor-pointer outline-none focus-visible:bg-hl", selected && "!bg-accent-soft")}
                    data-row-id={row.id}
                    key={row.id}
                    onClick={() => selectRow(row)}
                    onDoubleClick={() => openRowChart(row)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") openRowChart(row)
                    }}
                    tabIndex={0}
                  >
                    <td className="l rank">#{row.rank}</td>
                    <td className="l">
                      <button
                        className="symlink"
                        onClick={(event) => {
                          event.stopPropagation()
                          openRowChart(row)
                        }}
                        type="button"
                      >
                        {row.symbol}
                      </button>
                      <span className="block max-w-[200px] truncate font-sans text-2xs text-muted-text">
                        {row.name ?? row.fyers_symbol}
                      </span>
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5">
                        <b className="text-foreground">{row.technical_score?.toFixed(1) ?? "—"}</b>
                        {row.score_grade ? <GradeChip grade={row.score_grade} /> : <span className="gchip g-D">LEGACY</span>}
                      </span>
                    </td>
                    <td>{fmtNum(row.close_price)}</td>
                    <td className="l">
                      <TrendCell row={row} />
                    </td>
                    <td>{Number.isFinite(row.pct_from_52w_high) ? `${Math.abs(row.pct_from_52w_high * 100).toFixed(1)}%` : "—"}</td>
                    <td className="font-semibold text-foreground">{row.rs_rating}</td>
                    <td>{fmtNum(row.adtv_crore, 1)}</td>
                    <td className="l">
                      {row.fundamental_selected ? (
                        <span className="inline-flex items-center gap-1 text-wa">
                          <StarIcon aria-hidden="true" className="size-3 fill-current" />
                          Top 20{row.fundamental_selection_rank ? ` · #${row.fundamental_selection_rank}` : ""}
                        </span>
                      ) : (
                        <span className="text-muted-text">—</span>
                      )}
                    </td>
                    <td className="l">
                      <VcpVisionStatusBadge
                        aiVerdict={row.vcp_vision?.ai_verdict ?? null}
                        compact
                        status={row.vcp_vision?.status ?? null}
                      />
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-0.5">
                        <RowAction label="Open chart" onClick={() => openRowChart(row)}>
                          <LineChart aria-hidden="true" className="size-3.5" />
                        </RowAction>
                        <RowAction
                          label={row.vcp_vision ? "Review VCP vision analysis" : "Analyze VCP with vision"}
                          onClick={() => setVisionResult(row)}
                        >
                          <FlaskConical aria-hidden="true" className="size-3.5" />
                        </RowAction>
                        <RowAction
                          disabled={!row.fundamental_selected || proposalPending}
                          label={
                            row.fundamental_selected
                              ? "Generate a proposal for this stock"
                              : "Only the fundamental shortlist (Top 20) can generate a proposal"
                          }
                          onClick={() => triggerProposal(row)}
                        >
                          {proposalPending ? (
                            <Spinner className="size-3.5" />
                          ) : (
                            <Sparkles aria-hidden="true" className={cn("size-3.5", !row.fundamental_selected && "opacity-30")} />
                          )}
                        </RowAction>
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex flex-none flex-wrap items-center justify-between gap-3 border-t border-border bg-surface px-4 py-1.5 font-mono text-2xs text-muted-text">
        <span>
          Showing <b className="text-fg2">{totalItems === 0 ? 0 : (pageIndex - 1) * (pageSize || totalItems) + 1}</b>–
          <b className="text-fg2">{pageSize === 0 ? totalItems : Math.min(pageIndex * pageSize, totalItems)}</b> of{" "}
          <b className="text-fg2">{totalItems}</b>
        </span>
        {pageSize > 0 && totalPages > 1 && (
          <span className="flex items-center gap-1.5">
            <Button aria-label="First page" disabled={pageIndex <= 1} onClick={() => setView({ page: null })} size="icon-xs" type="button" variant="outline">
              <ChevronsLeft className="size-3.5" />
            </Button>
            <Button
              aria-label="Previous page"
              disabled={pageIndex <= 1}
              onClick={() => setView({ page: String(pageIndex - 1) })}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronLeft className="size-3.5" />
            </Button>
            <span className="px-2">
              Page <b className="text-fg2">{pageIndex}</b> of <b className="text-fg2">{totalPages}</b>
            </span>
            <Button
              aria-label="Next page"
              disabled={pageIndex >= totalPages}
              onClick={() => setView({ page: String(pageIndex + 1) })}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronRight className="size-3.5" />
            </Button>
            <Button
              aria-label="Last page"
              disabled={pageIndex >= totalPages}
              onClick={() => setView({ page: String(totalPages) })}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronsRight className="size-3.5" />
            </Button>
          </span>
        )}
      </div>

      {visionResult && (
        <VcpVisionSheet
          initialAnalysisId={visionResult.vcp_vision?.id ?? null}
          onOpenChange={(next) => {
            if (!next) setVisionResult(null)
          }}
          open
          result={visionResult}
        />
      )}
    </section>
  )
}
