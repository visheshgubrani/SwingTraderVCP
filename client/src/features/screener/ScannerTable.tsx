import {
  LineChart,
  Play,
  XCircle,
} from "lucide-react"

import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Spinner } from "@/components/ui/spinner"
import type {
  ScanResult,
  ScanRun,
  TechnicalScoreGrade,
} from "@/features/screener/api"
import { cn } from "@/lib/utils"

interface ScannerTableProps {
  items: ScanResult[]
  runs: ScanRun[]
  selectedRunId: string | null
  selectedResultId: string | null
  activeRun?: ScanRun
  errorMessage: string | null
  isError: boolean
  isLoading: boolean
  isRunning: boolean
  workflowMessage: string | null
  workflowError: boolean
  onSelectRun: (runId: string) => void
  onSelectResult: (result: ScanResult) => void
  onPlanTrade?: (result: ScanResult) => void
  onRunScan: () => void
  onRetry: () => void
}

function formatRun(run: ScanRun) {
  const date = new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(run.created_at))
  return `${date} · ${run.status} · ${run.passing_count} setups`
}

function gradeVariant(grade: TechnicalScoreGrade | null) {
  if (grade === "A") return "default" as const
  if (grade === "B") return "secondary" as const
  if (grade === "D") return "destructive" as const
  return "outline" as const
}

export function ScannerTable({
  items,
  runs,
  selectedRunId,
  selectedResultId,
  activeRun,
  errorMessage,
  isError,
  isLoading,
  isRunning,
  workflowMessage,
  workflowError,
  onSelectRun,
  onSelectResult,
  onPlanTrade,
  onRunScan,
  onRetry,
}: ScannerTableProps) {
  return (
    <div className="flex h-full flex-col bg-background font-mono text-xs">
      <div className="flex min-h-10 shrink-0 items-center justify-between gap-3 border-b bg-card px-3 py-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-semibold">VCP SCOREBOARD</span>
          <Badge variant="outline">
            {activeRun?.passing_count ?? items.length} ranked setups
          </Badge>
          <NativeSelect
            aria-label="Scanner run history"
            className="h-7 min-w-64 text-xs"
            disabled={runs.length === 0}
            onChange={(event) => onSelectRun(event.target.value)}
            value={selectedRunId ?? ""}
          >
            {runs.length === 0 && (
              <NativeSelectOption value="">No scanner runs</NativeSelectOption>
            )}
            {runs.map((run) => (
              <NativeSelectOption key={run.id} value={run.id}>
                {formatRun(run)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          {activeRun && (
            <Badge
              variant={activeRun.status === "failed" ? "destructive" : "secondary"}
            >
              {activeRun.status}
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-2">
          {workflowMessage && (
            <span
              className={cn(
                "max-w-96 truncate",
                workflowError ? "text-destructive" : "text-muted-foreground",
              )}
              title={workflowMessage}
            >
              {workflowMessage}
            </span>
          )}
          <Button
            disabled={isRunning}
            onClick={onRunScan}
            size="sm"
            type="button"
            variant="outline"
          >
            {isRunning ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <Play data-icon="inline-start" />
            )}
            {isRunning ? "SYNCING / SCORING" : "RUN EOD SCAN"}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Spinner />
            Loading scanner results…
          </div>
        ) : isError ? (
          <div className="flex h-full items-center justify-center p-6">
            <Alert className="max-w-xl" variant="destructive">
              <XCircle aria-hidden="true" />
              <AlertTitle>Could not load scanner results</AlertTitle>
              <AlertDescription>
                {errorMessage ?? "The scanner results API is unavailable."}
              </AlertDescription>
              <AlertAction>
                <Button
                  onClick={onRetry}
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
            <XCircle aria-hidden="true" className="text-destructive" />
            <strong>Scanner run failed</strong>
            <span className="max-w-xl text-muted-foreground">
              {activeRun.error_message ?? "The worker did not provide an error message."}
            </span>
          </div>
        ) : activeRun?.status === "queued" || activeRun?.status === "running" ? (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Spinner />
            Technical score engine is {activeRun.status}…
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
            <LineChart aria-hidden="true" className="text-muted-foreground" />
            <strong>No eligible setups</strong>
            <span className="text-muted-foreground">
              {activeRun
                ? "This run completed without a stock meeting the broad technical eligibility rules."
                : "Run the EOD scanner to build the first scored shortlist."}
            </span>
          </div>
        ) : (
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 border-b bg-card text-[10px] uppercase text-muted-foreground">
              <tr>
                <th className="w-12 px-3 py-1.5 text-center">Rank</th>
                <th className="px-3 py-1.5 text-right">Score</th>
                <th className="px-3 py-1.5 text-center">Grade</th>
                <th className="px-3 py-1.5">Symbol</th>
                <th className="px-3 py-1.5 text-right">Close</th>
                <th className="px-3 py-1.5 text-right">SMA 50</th>
                <th className="px-3 py-1.5 text-right">SMA 200</th>
                <th className="px-3 py-1.5 text-right">Below 52W high</th>
                <th className="px-3 py-1.5 text-center">RS</th>
                <th className="px-3 py-1.5 text-center">Setup</th>
                <th className="px-3 py-1.5 text-center">Funda</th>
                <th className="w-28 px-3 py-1.5 text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.map((row) => {
                const selected = selectedResultId === row.id
                return (
                  <tr
                    className={cn(
                      "cursor-pointer transition-colors",
                      selected
                        ? "bg-accent text-accent-foreground font-medium"
                        : "hover:bg-muted/50",
                    )}
                    key={row.id}
                    onClick={() => onSelectResult(row)}
                  >
                    <td className="px-3 py-2 text-center font-semibold">
                      #{row.rank}
                    </td>
                    <td className="px-3 py-2 text-right font-semibold">
                      {row.technical_score?.toFixed(2) ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <Badge variant={gradeVariant(row.score_grade)}>
                        {row.score_grade ?? "Legacy"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">
                      <strong className="block">{row.symbol}</strong>
                      <span className="block max-w-48 truncate text-[10px] text-muted-foreground">
                        {row.name ?? row.fyers_symbol}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right font-semibold">
                      ₹{row.close_price.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-right text-muted-foreground">
                      ₹{row.sma_50.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-right text-muted-foreground">
                      ₹{row.sma_200.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {(row.pct_from_52w_high * 100).toFixed(2)}%
                    </td>
                    <td className="px-3 py-2 text-center">{row.rs_rating}</td>
                    <td className="px-3 py-2 text-center">
                      <Badge variant="outline">
                        {row.technical_score === null ? "Legacy" : "Scored"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <Badge variant={row.fundamental_selected ? "secondary" : "outline"}>
                        {row.fundamental_selected ? "Top 20" : "Technical only"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Button
                          onClick={(event) => {
                            event.stopPropagation()
                            onSelectResult(row)
                          }}
                          size="icon-sm"
                          title="Load chart"
                          type="button"
                          variant="ghost"
                        >
                          <LineChart data-icon="inline-start" />
                        </Button>
                        <Button
                          onClick={(event) => {
                            event.stopPropagation()
                            onPlanTrade?.(row)
                          }}
                          size="sm"
                          type="button"
                          variant="outline"
                        >
                          Plan
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
    </div>
  )
}
