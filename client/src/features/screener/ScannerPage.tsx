import { useEffect, useMemo, useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  FlaskConical,
  LineChart,
  Play,
  Search,
  Sparkles,
  XCircle,
} from "lucide-react"
import { useNavigate } from "react-router"

import { GradeChip, StatusChip, type StatusTone } from "@/components/terminal/bits"
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { useAuthStatus } from "@/features/auth/api"
import { useTriggerSingleProposal } from "@/features/proposals/api"
import {
  defaultScanRunId,
  productionScanRuns,
  type ScanResult,
  type ScanRun,
  useScanResults,
  useScanRuns,
} from "@/features/screener/api"
import { VcpVisionSheet, VcpVisionStatusBadge } from "@/features/screener/VcpVisionSheet"
import { useScanWorkflow } from "@/features/screener/useScanWorkflow"
import { cn } from "@/lib/utils"

function formatRun(run: ScanRun) {
  const date = run.as_of_date
    ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(
        new Date(`${run.as_of_date}T00:00:00+05:30`),
      )
    : new Intl.DateTimeFormat("en-IN", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(run.created_at))
  const version = run.technical_config.pipeline_version
    ?.replace("vcp_score_", "")
    .toUpperCase() ?? "LEGACY"
  return `EOD ${date} · ${version} · ${run.status} · ${run.passing_count} eligible setups`
}

export function ScannerPage() {
  const navigate = useNavigate()
  const authStatus = useAuthStatus()
  const scanRuns = useScanRuns()
  const scanWorkflow = useScanWorkflow(authStatus.data)
  const generateProposal = useTriggerSingleProposal()

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [gradeFilter, setGradeFilter] = useState<string>("ALL")
  const [pageSize, setPageSize] = useState<number>(25)
  const [currentPage, setCurrentPage] = useState<number>(1)
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null)
  const [visionResult, setVisionResult] = useState<ScanResult | null>(null)

  const productionRuns = useMemo(
    () => productionScanRuns(scanRuns.data),
    [scanRuns.data],
  )

  const activeRunId =
    selectedRunId ??
    defaultScanRunId(productionRuns)
  const activeRun = productionRuns.find((r) => r.id === activeRunId)
  const scanResults = useScanResults(activeRunId, activeRun?.status)

  useEffect(() => {
    if (!scanWorkflow.scanRunId) return
    setSelectedRunId(scanWorkflow.scanRunId)
    setCurrentPage(1)
  }, [scanWorkflow.scanRunId])

  // Filter items based on search and grade
  const filteredItems = useMemo(() => {
    const raw = scanResults.data ?? []
    return raw.filter((item) => {
      if (gradeFilter !== "ALL" && item.score_grade !== gradeFilter) {
        return false
      }
      if (searchQuery.trim() !== "") {
        const query = searchQuery.trim().toLowerCase()
        const matchesSymbol = item.symbol.toLowerCase().includes(query)
        const matchesName = item.name?.toLowerCase().includes(query)
        const matchesFyers = item.fyers_symbol.toLowerCase().includes(query)
        if (!matchesSymbol && !matchesName && !matchesFyers) {
          return false
        }
      }
      return true
    })
  }, [scanResults.data, gradeFilter, searchQuery])

  // Reset pagination when filter/search changes
  const totalItems = filteredItems.length
  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(totalItems / pageSize))
  const pageIndex = Math.min(currentPage, totalPages)

  const displayedItems = useMemo(() => {
    if (pageSize === 0) return filteredItems
    const start = (pageIndex - 1) * pageSize
    return filteredItems.slice(start, start + pageSize)
  }, [filteredItems, pageIndex, pageSize])

  const handleRunScan = () => {
    void scanWorkflow.start()
  }

  const handleSelectStock = (result: ScanResult) => {
    setSelectedResultId(result.id)
  }

  const handleOpenChartInWorkstation = (result: ScanResult) => {
    // Open the chart workspace on this exact symbol.
    navigate(`/?symbol=${encodeURIComponent(result.fyers_symbol)}`)
  }

  const runMetaDate = activeRun
    ? activeRun.as_of_date
      ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(
          new Date(activeRun.as_of_date + "T00:00:00+05:30"),
        )
      : ""
    : ""
  const runTone: StatusTone = !activeRun
    ? "off"
    : activeRun.status === "succeeded"
      ? "fill"
      : activeRun.status === "failed"
        ? "rej"
        : activeRun.status === "cancelled"
          ? "off"
          : "work"
  const runStateLabel = activeRun
    ? activeRun.status.toUpperCase()
    : productionRuns.length > 0
      ? "IDLE"
      : "NO RUNS"

  return (
    <section className="view h-full">
      {/* VCP Scoreboard header */}
      <div className="vhead">
        <div>
          <h2>
            VCP Scoreboard <span className="sub">top-ranked contraction setups</span>
          </h2>
          <p className="vmeta">
            {activeRun ? (
              <>
                EOD <b>{runMetaDate}</b> · {runStateLabel} · <b>{activeRun.passing_count}</b> eligible setups
              </>
            ) : scanRuns.data?.length ? (
              "No completed run yet — run the EOD scanner."
            ) : (
              "Connect the backend to load scanner runs."
            )}
          </p>
        </div>
        <div className="vhead-right">
          <StatusChip tone={runTone}>{runStateLabel}</StatusChip>
          <span className="fsel">
            <select
              aria-label="Scanner run history"
              disabled={productionRuns.length === 0}
              onChange={(event) => {
                setSelectedRunId(event.target.value)
                setCurrentPage(1)
              }}
              style={{ minWidth: 300, maxWidth: 340 }}
              value={activeRunId ?? ""}
            >
              {productionRuns.length === 0 && (
                <option value="">No scanner runs found</option>
              )}
              {productionRuns.map((run) => (
                <option key={run.id} value={run.id}>
                  {formatRun(run)}
                </option>
              ))}
            </select>
          </span>
          <button
            className="btn btn-primary"
            disabled={scanWorkflow.isBusy}
            onClick={handleRunScan}
            type="button"
          >
            {scanWorkflow.isBusy ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <Play aria-hidden="true" className="btn-ic fill-current" />
            )}
            {scanWorkflow.phase === "syncing"
              ? "SYNCING EOD"
              : scanWorkflow.phase === "attaching_scan"
                ? "OPENING SCAN"
                : scanWorkflow.phase === "queued"
                  ? "WAITING FOR WORKER"
                  : scanWorkflow.phase === "scanning"
                    ? "SCORING NIFTY 500"
                    : "RUN EOD SCAN"}
          </button>
        </div>
      </div>

      {/* Proposal generation feedback strip */}
      {(generateProposal.isError || generateProposal.isSuccess) && (
        <div
          className={cn(
            "flex-none border-b px-5 py-1.5 font-mono text-[11px]",
            generateProposal.isError
              ? "border-ko-soft bg-ko-soft text-ko"
              : "border-border-soft text-muted-text",
          )}
        >
          {generateProposal.isError
            ? generateProposal.error instanceof Error
              ? generateProposal.error.message
              : "Could not queue proposal generation."
            : generateProposal.data?.message}
        </div>
      )}

      {/* Filter row */}
      <div className="sfilter">
        <label className="fsearch">
          <Search aria-hidden="true" className="ic" />
          <input
            onChange={(event) => {
              setSearchQuery(event.target.value)
              setCurrentPage(1)
            }}
            placeholder="Search symbol or name"
            type="text"
            value={searchQuery}
          />
        </label>
        <span className="fsel">
          <select
            aria-label="Filter by grade"
            onChange={(event) => {
              setGradeFilter(event.target.value)
              setCurrentPage(1)
            }}
            value={gradeFilter}
          >
            <option value="ALL">Grade: Any</option>
            <option value="A">Grade: A</option>
            <option value="B">Grade: B</option>
            <option value="C">Grade: C</option>
            <option value="D">Grade: D</option>
          </select>
        </span>
        <span className="fsel">
          <select
            aria-label="Rows per page"
            onChange={(event) => {
              setPageSize(Number(event.target.value))
              setCurrentPage(1)
            }}
            value={pageSize}
          >
            <option value={10}>Rows: 10</option>
            <option value={25}>Rows: 25</option>
            <option value={50}>Rows: 50</option>
            <option value={100}>Rows: 100</option>
            <option value={0}>Rows: All</option>
          </select>
        </span>
        <span className="sfmeta">
          <b>{totalItems}</b> of {activeRun?.passing_count ?? totalItems} shown
        </span>
      </div>

      {/* Table area */}
      <div className="tscroll">
        {scanResults.isLoading ? (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Spinner />
            Loading technical setups…
          </div>
        ) : scanResults.isError ? (
          <div className="flex h-full items-center justify-center p-6">
            <Alert className="max-w-xl" variant="destructive">
              <XCircle aria-hidden="true" />
              <AlertTitle>Could not load scanner results</AlertTitle>
              <AlertDescription>
                {scanResults.error instanceof Error
                  ? scanResults.error.message
                  : "The scanner results API is unavailable."}
              </AlertDescription>
              <AlertAction>
                <Button
                  onClick={() => void scanResults.refetch()}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  Retry
                </Button>
              </AlertAction>
            </Alert>
          </div>
        ) : activeRun?.status === "failed" ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <XCircle aria-hidden="true" className="text-ko" />
            <strong>Scanner run failed</strong>
            <span className="max-w-xl text-muted-foreground">
              {activeRun.error_message ?? "The worker did not provide an error message."}
            </span>
          </div>
        ) : activeRun?.status === "queued" || activeRun?.status === "running" ? (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Spinner />
            {activeRun.status === "queued"
              ? "Personal scan is waiting for the scanner worker…"
              : "Technical scoring is running across the Nifty 500…"}
          </div>
        ) : displayedItems.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-muted-foreground">
            <Search aria-hidden="true" className="size-8 stroke-1 text-muted-text/50" />
            <strong>No matching setups found</strong>
            <span>
              {searchQuery || gradeFilter !== "ALL"
                ? "Try clearing your search or grade filters."
                : "Run an EOD scan to generate setups across the Nifty 500 universe."}
            </span>
          </div>
        ) : (
          <table className="tbl tbl-scan">
            <thead>
              <tr>
                <th className="l" style={{ minWidth: 48 }}>RANK</th>
                <th style={{ minWidth: 60 }}>SCORE</th>
                <th className="l" style={{ minWidth: 52 }}>GRADE</th>
                <th className="l" style={{ minWidth: 128 }}>SYMBOL</th>
                <th style={{ minWidth: 80 }}>CLOSE</th>
                <th style={{ minWidth: 80 }}>SMA 50</th>
                <th style={{ minWidth: 80 }}>SMA 150</th>
                <th style={{ minWidth: 80 }}>SMA 200</th>
                <th style={{ minWidth: 84 }}>BELOW 52W</th>
                <th style={{ minWidth: 56 }}>RS</th>
                <th className="l" style={{ minWidth: 74 }}>SETUP</th>
                <th className="l" style={{ minWidth: 100 }}>FUNDA</th>
                <th className="l" style={{ minWidth: 96 }}>VISION</th>
                <th className="l" style={{ minWidth: 240 }}>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {displayedItems.map((row) => {
                const selected = selectedResultId === row.id
                return (
                  <tr
                    className={cn("cursor-pointer", selected && "bg-accent-soft")}
                    key={row.id}
                    onClick={() => handleSelectStock(row)}
                  >
                    <td className="l rank">#{row.rank}</td>
                    <td style={{ fontWeight: 700, color: "var(--fg)" }}>
                      {row.technical_score?.toFixed(2) ?? "—"}
                    </td>
                    <td className="l">
                      {row.score_grade ? (
                        <GradeChip grade={row.score_grade} />
                      ) : (
                        <span className="sc-chip off"><i />LEGACY</span>
                      )}
                    </td>
                    <td className="l">
                      <strong style={{ color: "var(--fg)", fontSize: 12 }}>
                        {row.symbol}
                      </strong>
                      <span
                        className="block"
                        style={{
                          color: "var(--muted-text)",
                          fontSize: 10,
                          fontFamily: "var(--font-sans)",
                          maxWidth: 190,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {row.name ?? row.fyers_symbol}
                      </span>
                    </td>
                    <td>₹{row.close_price.toFixed(2)}</td>
                    <td>₹{row.sma_50.toFixed(2)}</td>
                    <td>₹{(row.sma_150 ?? 0).toFixed(2)}</td>
                    <td>₹{row.sma_200.toFixed(2)}</td>
                    <td>{(row.pct_from_52w_high * 100).toFixed(2)}%</td>
                    <td style={{ fontWeight: 700 }}>{row.rs_rating}</td>
                    <td className="l">
                      <span className={cn("sc-chip", row.technical_score === null ? "off" : "work")}>
                        <i />
                        {row.technical_score === null ? "Legacy" : "Scored"}
                      </span>
                    </td>
                    <td className="l">
                      <span className={cn("sc-chip", row.fundamental_selected ? "fill" : "off")}>
                        <i />
                        {row.fundamental_selected ? "Top 20" : "Technical only"}
                      </span>
                    </td>
                    <td className="l">
                      <VcpVisionStatusBadge
                        aiVerdict={row.vcp_vision?.ai_verdict ?? null}
                        compact
                        status={row.vcp_vision?.status ?? null}
                      />
                    </td>
                    <td className="l">
                      <span className="act">
                        <button
                          aria-label={row.vcp_vision ? "Review VCP vision analysis" : "Analyze VCP with vision"}
                          className="btn btn-line plan"
                          onClick={(event) => {
                            event.stopPropagation()
                            setVisionResult(row)
                          }}
                          type="button"
                        >
                          <FlaskConical aria-hidden="true" className="btn-ic" />
                          {row.vcp_vision ? "Review VCP" : "Analyze VCP"}
                        </button>
                        <button
                          aria-label="Generate a P10 proposal for this stock"
                          className="btn btn-line plan"
                          disabled={
                            !row.fundamental_selected ||
                            (generateProposal.isPending && generateProposal.variables === row.id)
                          }
                          onClick={(event) => {
                            event.stopPropagation()
                            generateProposal.mutate(row.id)
                          }}
                          title={
                            row.fundamental_selected
                              ? "Generate a P10 proposal for this stock only"
                              : "Only the P10 shortlist (Top 20) can generate a proposal"
                          }
                          type="button"
                        >
                          {generateProposal.isPending && generateProposal.variables === row.id ? (
                            <Spinner data-icon="inline-start" />
                          ) : (
                            <Sparkles aria-hidden="true" className="btn-ic" />
                          )}
                          Proposal
                        </button>
                        <button
                          aria-label="Open chart for this symbol"
                          className="btn btn-line plan"
                          onClick={(event) => {
                            event.stopPropagation()
                            handleOpenChartInWorkstation(row)
                          }}
                          type="button"
                        >
                          <LineChart aria-hidden="true" className="btn-ic" />
                          Chart
                        </button>
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination footer */}
      <div className="flex flex-none flex-wrap items-center justify-between gap-3 border-t border-border bg-surface px-4 py-1.5 font-mono text-[11px] text-muted-text">
        <span>
          Showing{" "}
          <b style={{ color: "var(--fg-2)" }}>
            {totalItems === 0 ? 0 : (pageIndex - 1) * (pageSize || totalItems) + 1}
          </b>{" "}
          to{" "}
          <b style={{ color: "var(--fg-2)" }}>
            {pageSize === 0 ? totalItems : Math.min(pageIndex * pageSize, totalItems)}
          </b>{" "}
          of <b style={{ color: "var(--fg-2)" }}>{totalItems}</b> filtered setups
        </span>
        {pageSize > 0 && totalPages > 1 && (
          <span className="flex items-center gap-1.5">
            <Button
              aria-label="First page"
              disabled={pageIndex <= 1}
              onClick={() => setCurrentPage(1)}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronsLeft className="size-3.5" />
            </Button>
            <Button
              aria-label="Previous page"
              disabled={pageIndex <= 1}
              onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronLeft className="size-3.5" />
            </Button>
            <span className="px-2 text-muted-text">
              Page <b style={{ color: "var(--fg-2)" }}>{pageIndex}</b> of{" "}
              <b style={{ color: "var(--fg-2)" }}>{totalPages}</b>
            </span>
            <Button
              aria-label="Next page"
              disabled={pageIndex >= totalPages}
              onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronRight className="size-3.5" />
            </Button>
            <Button
              aria-label="Last page"
              disabled={pageIndex >= totalPages}
              onClick={() => setCurrentPage(totalPages)}
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
