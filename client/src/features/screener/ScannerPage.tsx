import { useEffect, useMemo, useState } from "react"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Filter,
  LineChart,
  Play,
  Search,
  XCircle,
} from "lucide-react"
import { useNavigate } from "react-router"

import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Spinner } from "@/components/ui/spinner"
import { useAuthStatus } from "@/features/auth/api"
import {
  type ScanResult,
  type ScanRun,
  type TechnicalScoreGrade,
  useScanResults,
  useScanRuns,
} from "@/features/screener/api"
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
  return `EOD ${date} · ${run.status} · ${run.passing_count} eligible setups`
}

function gradeVariant(grade: TechnicalScoreGrade | null) {
  if (grade === "A") return "default" as const
  if (grade === "B") return "secondary" as const
  if (grade === "D") return "destructive" as const
  return "outline" as const
}

export function ScannerPage() {
  const navigate = useNavigate()
  const authStatus = useAuthStatus()
  const scanRuns = useScanRuns()
  const scanWorkflow = useScanWorkflow(authStatus.data)

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [gradeFilter, setGradeFilter] = useState<string>("ALL")
  const [pageSize, setPageSize] = useState<number>(25)
  const [currentPage, setCurrentPage] = useState<number>(1)
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null)

  // Default to latest successful run if not explicitly selected
  const activeRunId =
    selectedRunId ??
    scanRuns.data?.find((r) => r.status === "queued" || r.status === "running")?.id ??
    scanRuns.data?.find((r) => r.status === "succeeded")?.id ??
    scanRuns.data?.[0]?.id ??
    null
  const activeRun = scanRuns.data?.find((r) => r.id === activeRunId)
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

  const handleOpenChartInWorkstation = (_result: ScanResult) => {
    // Navigate back to workstation dashboard
    navigate("/")
  }

  return (
    <div className="flex h-full w-full flex-col bg-background font-mono text-xs">
      {/* Top Header Banner */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b bg-card px-4 py-2.5">
        <div className="flex items-center gap-3">
          {scanWorkflow.message && (
            <span
              className={cn(
                "max-w-96 truncate text-[11px]",
                scanWorkflow.phase === "failed"
                  ? "text-destructive"
                  : "text-muted-foreground",
              )}
              title={scanWorkflow.message}
            >
              {scanWorkflow.message}
            </span>
          )}
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
            <h1 className="text-sm font-bold tracking-tight text-foreground">
              VCP TECHNICAL SCREENER
            </h1>
          </div>
          <Badge variant="outline" className="font-mono text-[11px]">
            {activeRun?.passing_count ?? scanResults.data?.length ?? 0} total setups
          </Badge>
          {activeRun && (
            <Badge
              variant={activeRun.status === "failed" ? "destructive" : "secondary"}
              className="uppercase text-[10px]"
            >
              {activeRun.status}
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">Run History:</span>
            <NativeSelect
              aria-label="Scanner run history"
              className="h-8 min-w-64 text-xs"
              disabled={!scanRuns.data?.length}
              onChange={(e) => {
                setSelectedRunId(e.target.value)
                setCurrentPage(1)
              }}
              value={activeRunId ?? ""}
            >
              {(!scanRuns.data || scanRuns.data.length === 0) && (
                <NativeSelectOption value="">No scanner runs found</NativeSelectOption>
              )}
              {scanRuns.data?.map((run) => (
                <NativeSelectOption key={run.id} value={run.id}>
                  {formatRun(run)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>

          <Button
            disabled={scanWorkflow.isBusy}
            onClick={handleRunScan}
            size="sm"
            type="button"
            className="h-8 gap-1.5 font-bold uppercase"
          >
            {scanWorkflow.isBusy ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <Play className="size-3.5 fill-current" />
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
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b bg-card/60 px-4 py-2">
        <div className="flex flex-wrap items-center gap-3">
          {/* Search Input */}
          <div className="relative w-64">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-8 pl-8 text-xs placeholder:text-muted-foreground/60"
              onChange={(e) => {
                setSearchQuery(e.target.value)
                setCurrentPage(1)
              }}
              placeholder="Search symbol or name…"
              value={searchQuery}
            />
          </div>

          {/* Grade Filter */}
          <div className="flex items-center gap-1.5 border-l pl-3">
            <Filter className="size-3 text-muted-foreground" />
            <span className="text-[11px] text-muted-foreground">Grade:</span>
            {(["ALL", "A", "B", "C", "D"] as const).map((grade) => (
              <button
                key={grade}
                className={cn(
                  "h-7 rounded px-2.5 text-[11px] font-medium transition-colors",
                  gradeFilter === grade
                    ? "bg-primary text-primary-foreground font-semibold shadow-xs"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
                onClick={() => {
                  setGradeFilter(grade)
                  setCurrentPage(1)
                }}
                type="button"
              >
                {grade}
              </button>
            ))}
          </div>
        </div>

        {/* Page Size Selector */}
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">Rows per page:</span>
          <NativeSelect
            aria-label="Rows per page"
            className="h-7 w-20 text-xs"
            onChange={(e) => {
              setPageSize(Number(e.target.value))
              setCurrentPage(1)
            }}
            value={pageSize}
          >
            <NativeSelectOption value={10}>10</NativeSelectOption>
            <NativeSelectOption value={25}>25</NativeSelectOption>
            <NativeSelectOption value={50}>50</NativeSelectOption>
            <NativeSelectOption value={100}>100</NativeSelectOption>
            <NativeSelectOption value={0}>All</NativeSelectOption>
          </NativeSelect>
        </div>
      </div>

      {/* Main Table Content */}
      <div className="flex-1 overflow-auto">
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
                {scanResults.error instanceof Error ? scanResults.error.message : "The scanner results API is unavailable."}
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
            <XCircle className="text-destructive" />
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
            <Search className="size-8 stroke-1 text-muted-foreground/50" />
            <strong className="text-foreground">No matching setups found</strong>
            <span>
              {searchQuery || gradeFilter !== "ALL"
                ? "Try clearing your search or grade filters."
                : "Run an EOD scan to generate setups across the Nifty 500 universe."}
            </span>
          </div>
        ) : (
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 z-10 border-b bg-card text-[10px] uppercase text-muted-foreground shadow-xs">
              <tr>
                <th className="w-12 px-3 py-2 text-center">Rank</th>
                <th className="px-3 py-2 text-right">Score</th>
                <th className="px-3 py-2 text-center">Grade</th>
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2 text-right">Close</th>
                <th className="px-3 py-2 text-right">SMA 50</th>
                <th className="px-3 py-2 text-right">SMA 150</th>
                <th className="px-3 py-2 text-right">SMA 200</th>
                <th className="px-3 py-2 text-right">Below 52W High</th>
                <th className="px-3 py-2 text-center">RS Rating</th>
                <th className="px-3 py-2 text-center">Setup</th>
                <th className="px-3 py-2 text-center">Funda</th>
                <th className="w-28 px-3 py-2 text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {displayedItems.map((row) => {
                const selected = selectedResultId === row.id
                return (
                  <tr
                    className={cn(
                      "cursor-pointer transition-colors hover:bg-muted/40",
                      selected && "bg-accent/80 text-accent-foreground font-medium",
                    )}
                    key={row.id}
                    onClick={() => handleSelectStock(row)}
                  >
                    <td className="px-3 py-2.5 text-center font-bold text-muted-foreground">
                      #{row.rank}
                    </td>
                    <td className="px-3 py-2.5 text-right font-bold text-foreground">
                      {row.technical_score?.toFixed(2) ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <Badge variant={gradeVariant(row.score_grade)} className="font-bold">
                        {row.score_grade ?? "Legacy"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2.5">
                      <strong className="block text-foreground">{row.symbol}</strong>
                      <span className="block max-w-56 truncate text-[10px] text-muted-foreground">
                        {row.name ?? row.fyers_symbol}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-bold text-foreground">
                      ₹{row.close_price.toFixed(2)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-muted-foreground">
                      ₹{row.sma_50.toFixed(2)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-muted-foreground">
                      ₹{(row.sma_150 ?? 0).toFixed(2)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-muted-foreground">
                      ₹{row.sma_200.toFixed(2)}
                    </td>
                    <td className="px-3 py-2.5 text-right font-medium">
                      {(row.pct_from_52w_high * 100).toFixed(2)}%
                    </td>
                    <td className="px-3 py-2.5 text-center font-bold">
                      {row.rs_rating}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <Badge variant="outline">
                        {row.technical_score === null ? "Legacy" : "Scored"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <Badge variant={row.fundamental_selected ? "secondary" : "outline"}>
                        {row.fundamental_selected ? "Top 20" : "Technical only"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleOpenChartInWorkstation(row)
                          }}
                          size="sm"
                          title="Open in Workstation"
                          type="button"
                          variant="outline"
                          className="h-7 gap-1 px-2 text-[11px]"
                        >
                          <LineChart className="size-3" />
                          Chart
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination Footer */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t bg-card px-4 py-2 text-xs">
        <div className="text-muted-foreground">
          Showing{" "}
          <span className="font-semibold text-foreground">
            {totalItems === 0 ? 0 : (pageIndex - 1) * (pageSize || totalItems) + 1}
          </span>{" "}
          to{" "}
          <span className="font-semibold text-foreground">
            {pageSize === 0 ? totalItems : Math.min(pageIndex * pageSize, totalItems)}
          </span>{" "}
          of <span className="font-semibold text-foreground">{totalItems}</span> filtered setups
        </div>

        {pageSize > 0 && totalPages > 1 && (
          <div className="flex items-center gap-1.5">
            <Button
              disabled={pageIndex <= 1}
              onClick={() => setCurrentPage(1)}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronsLeft className="size-3.5" />
            </Button>
            <Button
              disabled={pageIndex <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronLeft className="size-3.5" />
            </Button>

            <span className="px-2 text-muted-foreground">
              Page <strong className="text-foreground">{pageIndex}</strong> of{" "}
              <strong className="text-foreground">{totalPages}</strong>
            </span>

            <Button
              disabled={pageIndex >= totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronRight className="size-3.5" />
            </Button>
            <Button
              disabled={pageIndex >= totalPages}
              onClick={() => setCurrentPage(totalPages)}
              size="icon-xs"
              type="button"
              variant="outline"
            >
              <ChevronsRight className="size-3.5" />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
