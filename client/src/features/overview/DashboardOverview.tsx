import {
  AlertCircleIcon,
  CalendarClockIcon,
  CheckCircle2Icon,
  DatabaseIcon,
  LogInIcon,
  RefreshCwIcon,
  ScanSearchIcon,
  SquareIcon,
  WifiIcon,
} from "lucide-react"

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  useAuthStatus,
  useStartFyersLogin,
} from "@/features/auth/api"
import {
  isSyncActive,
  useCancelSync,
  useSyncStatus,
  useTriggerSync,
} from "@/features/historical/api"
import { useScanRuns } from "@/features/screener/api"
import {
  useP10Rollout,
  usePromoteP10Rollout,
  usePaperPortfolio,
} from "@/features/proposals/api"
import type { TickWorkerStatus } from "@/lib/MarketWSContext"
import { Input } from "@/components/ui/input"
import { useState } from "react"

interface DashboardOverviewProps {
  tickWorkerStatus: TickWorkerStatus | null
}

function formatDateTime(value?: string | null) {
  if (!value) return "Not available"
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Kolkata",
  }).format(new Date(value))
}

export function DashboardOverview({
  tickWorkerStatus,
}: DashboardOverviewProps) {
  const authStatus = useAuthStatus()
  const startLogin = useStartFyersLogin()
  const syncStatus = useSyncStatus()
  const triggerSync = useTriggerSync()
  const cancelSync = useCancelSync()
  const scanRuns = useScanRuns()
  const rollout = useP10Rollout()
  const promote = usePromoteP10Rollout()
  const paper = usePaperPortfolio(rollout.data?.stage === "paper")
  const [promoteBy, setPromoteBy] = useState("")
  const [promoteReason, setPromoteReason] = useState("")

  const sync = syncStatus.data
  const syncing = isSyncActive(sync)
  const latestScan = scanRuns.data?.[0]
  const progress =
    sync && sync.total_symbols > 0
      ? Math.round((sync.current_index / sync.total_symbols) * 100)
      : 0
  const requestError =
    (triggerSync.error instanceof Error && triggerSync.error.message) ||
    (cancelSync.error instanceof Error && cancelSync.error.message) ||
    null

  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>
            Operations <span className="sub">data ops · sync · rollout · controls</span>
          </h2>
          <p className="vmeta">
            Fyers auth · EOD sync · tick worker · P10 rollout · paper account
          </p>
        </div>
        <div className="vhead-right">
          {rollout.data && (
            <span className="note-demo">{rollout.data.stage.replaceAll("_", " ").toUpperCase()}</span>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 pt-1 font-mono text-xs">
      <div className="grid shrink-0 grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">FYERS AUTH</span>
            <WifiIcon aria-hidden="true" />
          </div>
          <strong className="text-lg">
            {authStatus.data?.healthy ? "Connected" : "Login required"}
          </strong>
          <span className="text-muted-foreground">
            {authStatus.data?.expires_at
              ? `Expires ${formatDateTime(authStatus.data.expires_at)}`
              : "No valid broker token"}
          </span>
          <Button
            disabled={startLogin.isPending}
            onClick={() => startLogin.mutate()}
            size="sm"
            type="button"
            variant={authStatus.data?.healthy ? "outline" : "default"}
          >
            <LogInIcon data-icon="inline-start" />
            {authStatus.data?.healthy ? "Re-authenticate" : "Login to Fyers"}
          </Button>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">LATEST STORED EOD</span>
            <CalendarClockIcon aria-hidden="true" />
          </div>
          <strong className="text-lg">
            {sync?.db_metrics.latest_candle_date ?? "No candle data"}
          </strong>
          <span className="text-muted-foreground">
            {sync?.db_metrics.symbols_at_latest_date ?? 0} /{" "}
            {sync?.db_metrics.nifty500_instruments ?? 0} symbols at latest date
          </span>
          <Badge variant={sync?.schedule.enabled ? "outline" : "destructive"}>
            {sync?.schedule.enabled
              ? `${sync.schedule.weekdays} · ${sync.schedule.time} ${sync.schedule.timezone}`
              : "Automatic sync disabled"}
          </Badge>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">CANDLE DATABASE</span>
            <DatabaseIcon aria-hidden="true" />
          </div>
          <strong className="text-lg">
            {(sync?.db_metrics.total_candles ?? 0).toLocaleString("en-IN")}
          </strong>
          <span className="text-muted-foreground">
            Daily candles across{" "}
            {sync?.db_metrics.nifty500_instruments ?? 0} active instruments
          </span>
          <Badge variant="secondary">PostgreSQL system of record</Badge>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">LATEST SCANNER RUN</span>
            <ScanSearchIcon aria-hidden="true" />
          </div>
          <strong className="text-lg">
            {latestScan ? latestScan.status.toUpperCase() : "No runs"}
          </strong>
          <span className="text-muted-foreground">
            {latestScan
              ? `${latestScan.passing_count} ranked setups · ${formatDateTime(latestScan.created_at)}`
              : "Run the EOD scanner from the chart workspace"}
          </span>
          {latestScan && (
            <Badge
              variant={
                latestScan.status === "failed" ? "destructive" : "outline"
              }
            >
              {latestScan.technical_config.pipeline_version ??
                latestScan.universe_code}
            </Badge>
          )}
        </section>
      </div>

      <section className="rounded-lg border bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-muted-foreground">P10 ROLLOUT</span>
          <Badge variant="outline">{rollout.data?.stage?.replaceAll("_", " ") ?? "…"}</Badge>
        </div>
        <p className="text-muted-foreground">
          Shadow blocks approve. Paper uses the ₹1,00,000 fake ledger and the same fill processors as live.
          {paper.data
            ? ` Cash ${Number(paper.data.cash_available).toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 })}.`
            : ""}
        </p>
        {rollout.data?.next_stage && rollout.data.required_confirmation && (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <Input
              aria-label="Rollout operator"
              className="h-8 w-40 text-[11px]"
              onChange={(event) => setPromoteBy(event.target.value)}
              placeholder="Changed by"
              value={promoteBy}
            />
            <Input
              aria-label="Rollout reason"
              className="h-8 min-w-48 flex-1 text-[11px]"
              onChange={(event) => setPromoteReason(event.target.value)}
              placeholder="Reason"
              value={promoteReason}
            />
            <Button
              disabled={
                promote.isPending
                || promoteBy.trim().length === 0
                || promoteReason.trim().length === 0
              }
              onClick={() => {
                const nextStage = rollout.data?.next_stage
                const confirmation = rollout.data?.required_confirmation
                if (nextStage && nextStage !== "shadow" && confirmation) {
                  promote.mutate({
                    targetStage: nextStage,
                    confirmation,
                    changedBy: promoteBy.trim(),
                    reason: promoteReason.trim(),
                  })
                }
              }}
              size="sm"
              type="button"
            >
              Promote to {rollout.data?.next_stage ? rollout.data.next_stage.replaceAll("_", " ") : "next stage"}
            </Button>
          </div>
        )}
        {rollout.data?.required_confirmation && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Confirmation phrase: {rollout.data.required_confirmation}
          </p>
        )}
        {promote.error instanceof Error && (
          <p className="mt-2 text-destructive">{promote.error.message}</p>
        )}
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="flex flex-col gap-4 rounded-lg border bg-card p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold">Historical EOD sync</h2>
              <p className="mt-1 text-muted-foreground">
                Incrementally fetch candles after each symbol&apos;s latest
                saved trading date.
              </p>
            </div>
            <Badge variant={syncing ? "default" : "outline"}>
              {sync?.state ?? "unavailable"}
            </Badge>
          </div>

          {syncing && sync && (
            <div className="flex flex-col gap-2">
              <div className="flex justify-between text-muted-foreground">
                <span>
                  {sync.state === "queued"
                    ? "Waiting for arq worker"
                    : `Syncing ${sync.current_symbol || "Nifty 500"}`}
                </span>
                <span>
                  {sync.current_index} / {sync.total_symbols}
                </span>
              </div>
              <div
                aria-label={`Historical sync ${progress}% complete`}
                className="h-2 overflow-hidden rounded-full bg-muted"
                role="progressbar"
              >
                <div
                  className="h-full rounded-full bg-primary transition-[width]"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <span className="text-muted-foreground">{progress}% complete</span>
            </div>
          )}

          {sync && !syncing && sync.state !== "idle" && (
            <Alert variant={sync.state === "failed" ? "destructive" : "default"}>
              {sync.state === "succeeded" ? (
                <CheckCircle2Icon aria-hidden="true" />
              ) : (
                <AlertCircleIcon aria-hidden="true" />
              )}
              <AlertTitle>Last sync: {sync.state}</AlertTitle>
              <AlertDescription>
                {sync.logs.at(-1) ??
                  `${sync.candles_upserted} candles were saved.`}
              </AlertDescription>
            </Alert>
          )}

          {requestError && (
            <Alert variant="destructive">
              <AlertCircleIcon aria-hidden="true" />
              <AlertTitle>Sync request failed</AlertTitle>
              <AlertDescription>{requestError}</AlertDescription>
            </Alert>
          )}

          <div className="flex gap-2">
            {syncing ? (
              <Button
                disabled={cancelSync.isPending}
                onClick={() => cancelSync.mutate()}
                type="button"
                variant="destructive"
              >
                {cancelSync.isPending ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <SquareIcon data-icon="inline-start" />
                )}
                Cancel sync
              </Button>
            ) : (
              <Button
                disabled={
                  !authStatus.data?.healthy || triggerSync.isPending
                }
                onClick={() =>
                  triggerSync.mutate({ backfillYears: 1 })
                }
                type="button"
              >
                {triggerSync.isPending ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <RefreshCwIcon data-icon="inline-start" />
                )}
                Sync latest EOD data
              </Button>
            )}
          </div>
        </section>

        <section className="flex min-h-80 flex-col gap-4 rounded-lg border bg-card p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold">Operational status</h2>
              <p className="mt-1 text-muted-foreground">
                Only status reported by implemented P0–P4 services is shown.
              </p>
            </div>
            <Badge variant="outline">
              Tick worker: {tickWorkerStatus?.status ?? "unknown"}
            </Badge>
          </div>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="rounded-lg border bg-background p-3">
              <span className="text-muted-foreground">Tick ingestion</span>
              <strong className="mt-1 block">
                {tickWorkerStatus?.status ?? "No heartbeat"}
              </strong>
              <span className="mt-1 block text-muted-foreground">
                {tickWorkerStatus?.symbol_count ?? 0} subscribed symbols
              </span>
            </div>
            <div className="rounded-lg border bg-background p-3">
              <span className="text-muted-foreground">Scheduler</span>
              <strong className="mt-1 block">
                {sync?.schedule.enabled ? "Configured" : "Disabled"}
              </strong>
              <span className="mt-1 block text-muted-foreground">
                Status requires the supervised arq worker to be running.
              </span>
            </div>
          </div>

          <div className="flex min-h-44 flex-1 flex-col gap-1 overflow-y-auto rounded-lg border bg-background p-3">
            <span className="mb-2 text-muted-foreground">Latest sync output</span>
            {sync?.logs.length ? (
              sync.logs.slice(-80).map((line, index) => (
                <span
                  className={
                    line.includes("ERROR")
                      ? "text-destructive"
                      : "text-foreground"
                  }
                  key={`${index}-${line}`}
                >
                  {line}
                </span>
              ))
            ) : (
              <span className="text-muted-foreground">
                No historical sync output is available.
              </span>
            )}
          </div>
        </section>
      </div>
    </div>
    </section>
  )
}
