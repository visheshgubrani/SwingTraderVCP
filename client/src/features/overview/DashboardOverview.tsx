import { useState, type ReactNode } from "react"
import { HistoryIcon, LogInIcon, RefreshCwIcon, ShieldCheckIcon, SquareIcon } from "lucide-react"

import { Panel, SectionLabel, Stat, type StatTone } from "@/components/terminal/panel"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import {
  useAutomationControls,
  useFundamentalControls,
  useKillSwitch,
  useReconciliationRuns,
  useRiskPolicy,
  useSetAutomationControl,
  useSetFundamentalControl,
  useSetKillSwitch,
  useTriggerReconciliation,
} from "@/features/admin/api"
import { useAuthStatus, useStartFyersLogin } from "@/features/auth/api"
import { isSyncActive, useCancelSync, useSyncStatus, useTriggerSync } from "@/features/historical/api"
import { MarketContextAdminPanel } from "@/features/proposals/MarketContextPanel"
import {
  useEntrySupervisorStatus,
  usePaperPortfolio,
  useP10Rollout,
  usePromoteP10Rollout,
  useProposalBatches,
  type P10RolloutStage,
} from "@/features/proposals/api"
import { productionScanRuns, useScanRuns } from "@/features/screener/api"
import { useExecutionStatus } from "@/features/trade/api"
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, fmtRelative, toNum } from "@/lib/format"
import type { TickWorkerStatus } from "@/lib/MarketWSContext"
import { cn } from "@/lib/utils"

interface DashboardOverviewProps {
  tickWorkerStatus: TickWorkerStatus | null
}

const STAGES: P10RolloutStage[] = ["shadow", "paper", "reduced_live", "full_live"]

function label(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "—"
}

/* ── health ── */

function HealthRow({ tickWorkerStatus }: DashboardOverviewProps) {
  const auth = useAuthStatus()
  const startLogin = useStartFyersLogin()
  const sync = useSyncStatus()
  const scanRuns = useScanRuns()
  const batches = useProposalBatches(1)
  const supervisor = useEntrySupervisorStatus()
  const reconciliation = useReconciliationRuns(1)
  const execution = useExecutionStatus()
  const rollout = useP10Rollout()
  const paper = usePaperPortfolio(rollout.data?.stage === "paper")

  const metrics = sync.data?.db_metrics
  const lastScan = productionScanRuns(scanRuns.data).find((run) => run.status === "succeeded")
  const latestAttempt = productionScanRuns(scanRuns.data)[0]
  const batch = batches.data?.[0]
  const recon = reconciliation.data?.[0]
  const feedRunning = (tickWorkerStatus?.status ?? "").toLowerCase().includes("run")
  const eodCurrent = metrics?.latest_candle_date && metrics.latest_candle_date === metrics.expected_latest_candle_date

  const tiles: { label: string; value: string; sub?: string; tone: StatTone; loading?: boolean; action?: ReactNode }[] = [
    {
      label: "Broker auth (Fyers)",
      value: auth.isError ? "Unavailable" : auth.data?.healthy ? "Connected" : "Login required",
      sub: auth.data?.expires_at ? `Expires ${fmtDateTime(auth.data.expires_at)}` : "No valid session",
      tone: auth.data?.healthy ? "ok" : "bad",
      loading: auth.isLoading,
      action: (
        <Button disabled={startLogin.isPending} onClick={() => startLogin.mutate()} size="xs" type="button" variant={auth.data?.healthy ? "outline" : "default"}>
          <LogInIcon data-icon="inline-start" />
          {auth.data?.healthy ? "Re-authenticate" : "Login to Fyers"}
        </Button>
      ),
    },
    {
      label: "Market-data feed",
      value: tickWorkerStatus ? label(tickWorkerStatus.status) : "No heartbeat",
      sub: tickWorkerStatus
        ? `${tickWorkerStatus.symbol_count ?? 0} symbols · ${tickWorkerStatus.timestamp ? fmtRelative(tickWorkerStatus.timestamp) : "—"}`
        : "Tick worker not reporting",
      tone: feedRunning ? "ok" : "warn",
    },
    {
      label: "EOD candles",
      value: metrics?.latest_candle_date ? fmtDate(metrics.latest_candle_date) : "No data",
      sub: metrics ? `${metrics.symbols_at_latest_date}/${metrics.nifty500_instruments} symbols · ${fmtNum(metrics.total_candles, 0)} candles` : undefined,
      tone: eodCurrent ? "ok" : "warn",
      loading: sync.isLoading,
    },
    {
      label: "Last scan",
      value: lastScan ? fmtDate(lastScan.as_of_date ?? lastScan.created_at) : "None yet",
      sub: lastScan
        ? `${lastScan.passing_count} setups${latestAttempt && latestAttempt.id !== lastScan.id ? ` · latest run ${latestAttempt.status}` : ""}`
        : "Run the EOD scan from Scanner",
      tone: latestAttempt?.status === "failed" ? "bad" : lastScan ? "ok" : "neutral",
      loading: scanRuns.isLoading,
    },
    {
      label: "Proposal batch",
      value: batch ? label(batch.status) : "No runs",
      sub: batch ? `${fmtDateTime(batch.created_at)} · ${batch.proposals_generated} proposed` : undefined,
      tone: !batch ? "neutral" : batch.status === "completed" ? "ok" : batch.status === "running" ? "info" : "bad",
      loading: batches.isLoading,
    },
    {
      label: "Entry supervisor",
      value: supervisor.data ? (supervisor.data.status === "active" ? "Active" : "Inactive") : "Unknown",
      sub: supervisor.data
        ? `${supervisor.data.armed_legs_count} armed · market data ${supervisor.data.market_data.ready ? "ready" : supervisor.data.market_data.status}`
        : undefined,
      tone: supervisor.data?.status === "active" && supervisor.data.market_data.ready ? "ok" : "warn",
      loading: supervisor.isLoading,
    },
    {
      label: "Reconciliation",
      value: recon ? label(recon.status) : "Never run",
      sub: recon ? `${fmtRelative(recon.started_at)} · ${recon.discrepancies_found} discrepancies` : undefined,
      tone: !recon ? "neutral" : recon.status === "failed" || recon.discrepancies_found > 0 ? "warn" : recon.status === "running" ? "info" : "ok",
      loading: reconciliation.isLoading,
    },
    {
      label: "Execution",
      value: `${(execution.data?.execution_mode ?? "paper").toUpperCase()} · ${label(rollout.data?.stage).toUpperCase()}`,
      sub: paper.data
        ? `Paper cash ${fmtMoney(paper.data.cash_available, { decimals: 0 })}`
        : execution.data?.execution_mode === "live"
          ? `Live order placement ${execution.data.live_order_placement_enabled ? "armed" : "disarmed"}`
          : undefined,
      tone: execution.data?.execution_mode === "live" ? "warn" : "info",
      loading: execution.isLoading || rollout.isLoading,
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      {tiles.map((tile) => (
        <Stat action={tile.action} dot key={tile.label} label={tile.label} loading={tile.loading} sub={tile.sub} tone={tile.tone} value={tile.value} />
      ))}
    </div>
  )
}

/* ── controls ── */

function ControlRow({
  title,
  description,
  engaged,
  engagedText,
  releasedText,
  engageLabel,
  releaseLabel,
  meta,
  danger = false,
  disabled = false,
  pending,
  error,
  onSet,
}: {
  title: string
  description: string
  engaged: boolean | undefined
  engagedText: string
  releasedText: string
  engageLabel: string
  releaseLabel: string
  meta?: string
  danger?: boolean
  disabled?: boolean
  pending: boolean
  error: unknown
  onSet: (engage: boolean, reason: string) => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState("")
  const next = !engaged

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border-soft py-3 last:border-b-0">
      <div className="min-w-[220px] flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{title}</span>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-2 text-2xs font-semibold leading-5",
              engaged === undefined ? "bg-hl text-muted-text" : engaged ? (danger ? "bg-ko-soft text-ko" : "bg-wa-soft text-wa") : "bg-ok-soft text-ok",
            )}
          >
            {engaged === undefined ? "Unknown" : engaged ? engagedText : releasedText}
          </span>
        </div>
        <p className="mt-0.5 text-xs text-muted-text">{description}</p>
        {meta && <p className="mt-0.5 font-mono text-2xs text-muted-text">{meta}</p>}
      </div>
      <Button
        disabled={disabled || engaged === undefined || pending}
        onClick={() => {
          setReason("")
          setOpen(true)
        }}
        size="sm"
        type="button"
        variant={next && danger ? "destructive" : "outline"}
      >
        {pending && <Spinner data-icon="inline-start" />}
        {engaged ? releaseLabel : engageLabel}
      </Button>
      <AlertDialog onOpenChange={setOpen} open={open}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {engaged ? releaseLabel : engageLabel} — {title}?
            </AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <Input aria-label="Reason" autoFocus onChange={(event) => setReason(event.target.value)} placeholder="Reason (recorded)" value={reason} />
          {error instanceof Error && <p className="text-xs text-ko">{error.message}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              disabled={!reason.trim() || pending}
              onClick={() => {
                void onSet(next, reason.trim()).then(() => setOpen(false)).catch(() => undefined)
              }}
              type="button"
              variant={next && danger ? "destructive" : "default"}
            >
              {pending && <Spinner data-icon="inline-start" />}
              Confirm
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ControlsPanel() {
  const kill = useKillSwitch()
  const setKill = useSetKillSwitch()
  const controls = useAutomationControls()
  const setControl = useSetAutomationControl()
  const fundamentals = useFundamentalControls()
  const setFundamental = useSetFundamentalControl()
  const byKey = new Map((controls.data ?? []).map((control) => [control.control_key, control]))
  const proposalPause = byKey.get("proposal_processing_paused")
  const entryPause = byKey.get("new_entries_paused")
  const metaOf = (state?: { changed_by: string; changed_at: string; reason: string | null }) =>
    state ? `${fmtDateTime(state.changed_at)} · ${state.changed_by}${state.reason ? ` · ${state.reason}` : ""}` : undefined

  return (
    <Panel description="Every change asks for a reason and is recorded. Exits and reconciliation keep running under the pauses." title="Safety controls">
      <ControlRow
        danger
        description="Kill switch = no automated orders (entries and exits). It is not a substitute for being flat."
        engageLabel="Engage kill switch"
        engaged={kill.data?.enabled}
        engagedText="Engaged — no automated orders"
        error={setKill.error}
        meta={metaOf(kill.data ?? undefined)}
        onSet={(enabled, reason) => setKill.mutateAsync({ enabled, reason })}
        pending={setKill.isPending}
        releaseLabel="Resume automation"
        releasedText="Automation on"
        title="Global kill switch"
      />
      <ControlRow
        description="Blocks new initial and add legs. Open positions stay protected by the monitor."
        engageLabel="Pause new entries"
        engaged={entryPause?.enabled}
        engagedText="Paused"
        error={setControl.error}
        meta={metaOf(entryPause)}
        onSet={(enabled, reason) => setControl.mutateAsync({ control: "new_entries_paused", enabled, reason })}
        pending={setControl.isPending && setControl.variables?.control === "new_entries_paused"}
        releaseLabel="Resume entries"
        releasedText="Allowed"
        title="New-entry pause"
      />
      <ControlRow
        description="Stops chart rendering and Gemini inference for new proposal batches."
        engageLabel="Pause proposals"
        engaged={proposalPause?.enabled}
        engagedText="Paused"
        error={setControl.error}
        meta={metaOf(proposalPause)}
        onSet={(enabled, reason) => setControl.mutateAsync({ control: "proposal_processing_paused", enabled, reason })}
        pending={setControl.isPending && setControl.variables?.control === "proposal_processing_paused"}
        releaseLabel="Resume proposals"
        releasedText="Running"
        title="Proposal processing"
      />
      <ControlRow
        description="Pauses the P7 fundamentals pipeline (Upstox + NSE enrichment)."
        engageLabel="Pause pipeline"
        engaged={fundamentals.data?.processing.paused}
        engagedText="Paused"
        error={setFundamental.error}
        meta={metaOf(fundamentals.data?.processing)}
        onSet={(paused, reason) => setFundamental.mutateAsync({ control: "processing", paused, reason })}
        pending={setFundamental.isPending && setFundamental.variables?.control === "processing"}
        releaseLabel="Resume pipeline"
        releasedText="Running"
        title="Fundamentals pipeline"
      />
      <ControlRow
        description="Pauses the OpenRouter second-opinion pass. Python's deterministic fit keeps running."
        engageLabel="Pause AI"
        engaged={fundamentals.data?.ai.paused}
        engagedText="Paused"
        error={setFundamental.error}
        meta={metaOf(fundamentals.data?.ai)}
        onSet={(paused, reason) => setFundamental.mutateAsync({ control: "ai", paused, reason })}
        pending={setFundamental.isPending && setFundamental.variables?.control === "ai"}
        releaseLabel="Resume AI"
        releasedText="Running"
        title="Fundamentals AI"
      />
    </Panel>
  )
}

/* ── rollout & policy ── */

function RolloutPanel() {
  const rollout = useP10Rollout()
  const promote = usePromoteP10Rollout()
  const [open, setOpen] = useState(false)
  const [changedBy, setChangedBy] = useState("")
  const [reason, setReason] = useState("")
  const [phrase, setPhrase] = useState("")
  const data = rollout.data
  const currentIndex = data ? STAGES.indexOf(data.stage) : -1
  const nextStage = data?.next_stage
  const ready = Boolean(nextStage && data?.required_confirmation && phrase === data.required_confirmation && changedBy.trim() && reason.trim())

  return (
    <Panel
      actions={
        nextStage && (
          <Button
            onClick={() => {
              setPhrase("")
              setOpen(true)
            }}
            size="sm"
            type="button"
            variant="outline"
          >
            Promote to {label(nextStage)}…
          </Button>
        )
      }
      description="Owner-only and one step at a time. A stage never promotes itself."
      title="P10 rollout"
    >
      <ol className="flex flex-wrap items-center gap-1">
        {STAGES.map((stage, index) => (
          <li className="flex items-center gap-1" key={stage}>
            <span
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs font-semibold capitalize",
                index === currentIndex
                  ? "border-accent bg-accent-soft text-accent"
                  : index < currentIndex
                    ? "border-border-soft text-fg2"
                    : "border-border-soft text-muted-text",
              )}
            >
              {label(stage)}
            </span>
            {index < STAGES.length - 1 && <span className="text-muted-text">→</span>}
          </li>
        ))}
      </ol>
      {data && (
        <p className="mt-2 text-xs text-muted-text">
          Changed {fmtDateTime(data.changed_at)} by {data.changed_by}
          {data.reason ? ` — ${data.reason}` : ""}. Approvals {data.approvals_allowed ? "allowed" : "blocked"} in this stage.
        </p>
      )}
      {promote.isSuccess && <p className="mt-1 text-xs text-ok">Promoted.</p>}
      <AlertDialog onOpenChange={setOpen} open={open}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Promote rollout to {label(nextStage)}?</AlertDialogTitle>
            <AlertDialogDescription>
              Type <b className="font-mono text-foreground">{data?.required_confirmation}</b> to confirm.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2">
            <Input aria-label="Confirmation phrase" onChange={(event) => setPhrase(event.target.value)} placeholder="Confirmation phrase" value={phrase} />
            <Input aria-label="Rollout operator" onChange={(event) => setChangedBy(event.target.value)} placeholder="Your name" value={changedBy} />
            <Input aria-label="Rollout reason" onChange={(event) => setReason(event.target.value)} placeholder="Reason" value={reason} />
          </div>
          {promote.error instanceof Error && <p className="text-xs text-ko">{promote.error.message}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              disabled={!ready || promote.isPending}
              onClick={() => {
                if (!nextStage || nextStage === "shadow" || !data?.required_confirmation) return
                promote.mutate(
                  { targetStage: nextStage, confirmation: phrase, changedBy: changedBy.trim(), reason: reason.trim() },
                  { onSuccess: () => setOpen(false) },
                )
              }}
              type="button"
            >
              {promote.isPending && <Spinner data-icon="inline-start" />}
              Promote
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Panel>
  )
}

function RiskPolicyPanel() {
  const policy = useRiskPolicy()
  const pct = (value: number | string | null | undefined) => {
    const n = toNum(value)
    return n === null ? "—" : `${fmtNum(n * 100, 1)}%`
  }
  const data = policy.data
  return (
    <Panel description="Read-only. Changing the policy creates a new immutable version." title={data ? `Risk policy · v${data.version} ${data.name}` : "Risk policy"}>
      {policy.isLoading ? (
        <p className="text-xs text-muted-text">Loading…</p>
      ) : !data ? (
        <p className="text-xs text-muted-text">No active risk policy.</p>
      ) : (
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs md:grid-cols-3">
          {(
            [
              ["Risk per trade", pct(data.risk_per_trade_pct)],
              ["Total open risk", pct(data.max_total_open_risk_pct)],
              ["Daily loss stop", pct(data.daily_loss_limit_pct)],
              ["Single name", pct(data.max_single_name_notional_pct)],
              ["Sector", pct(data.max_sector_notional_pct)],
              ["Correlation cluster", `${pct(data.max_cluster_notional_pct)} · ρ ≥ ${fmtNum(toNum(data.correlation_cluster_threshold))}`],
              ["Max positions", String(data.max_open_positions)],
              ["Stop streak limit", String(data.consecutive_stop_limit)],
              ["Deployable capital", fmtMoney(data.deployable_capital_override, { decimals: 0 })],
            ] as [string, string][]
          ).map(([name, value]) => (
            <div className="flex justify-between gap-2 border-b border-border-soft py-1" key={name}>
              <span className="text-muted-text">{name}</span>
              <span className="font-mono text-fg2">{value}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}

/* ── data jobs ── */

function SyncPanel() {
  const auth = useAuthStatus()
  const syncStatus = useSyncStatus()
  const triggerSync = useTriggerSync()
  const cancelSync = useCancelSync()
  const [mode, setMode] = useState<"latest" | "repair" | null>(null)
  const sync = syncStatus.data
  const syncing = isSyncActive(sync)
  const progress = sync && sync.total_symbols > 0 ? Math.round((sync.current_index / sync.total_symbols) * 100) : 0
  const requestError =
    (triggerSync.error instanceof Error && triggerSync.error.message) || (cancelSync.error instanceof Error && cancelSync.error.message) || null
  const run = (repair: boolean) => {
    setMode(repair ? "repair" : "latest")
    triggerSync.mutate({ backfillYears: 2, repairHistory: repair }, { onSettled: () => setMode(null) })
  }

  return (
    <Panel
      actions={
        syncing ? (
          <Button disabled={cancelSync.isPending} onClick={() => cancelSync.mutate()} size="sm" type="button" variant="destructive">
            {cancelSync.isPending ? <Spinner data-icon="inline-start" /> : <SquareIcon data-icon="inline-start" />}
            Cancel sync
          </Button>
        ) : (
          <>
            <Button disabled={!auth.data?.healthy || triggerSync.isPending} onClick={() => run(false)} size="sm" type="button">
              {mode === "latest" ? <Spinner data-icon="inline-start" /> : <RefreshCwIcon data-icon="inline-start" />}
              Sync latest EOD data
            </Button>
            <Button disabled={!auth.data?.healthy || triggerSync.isPending} onClick={() => run(true)} size="sm" type="button" variant="outline">
              {mode === "repair" ? <Spinner data-icon="inline-start" /> : <HistoryIcon data-icon="inline-start" />}
              Backfill 2 Years &amp; Repair
            </Button>
          </>
        )
      }
      description={
        sync?.schedule.enabled
          ? `Scheduled ${sync.schedule.weekdays} · ${sync.schedule.time} ${sync.schedule.timezone}`
          : "Automatic sync is disabled"
      }
      title={`EOD sync · ${label(sync?.state ?? "unavailable")}`}
    >
      <div className="flex flex-col gap-3">
        {syncing && sync && (
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-xs text-muted-text">
              <span>{sync.state === "queued" ? "Waiting for the arq worker" : `Syncing ${sync.current_symbol || "Nifty 500"}`}</span>
              <span className="font-mono">
                {sync.current_index}/{sync.total_symbols} · {progress}%
              </span>
            </div>
            <div
              aria-label="Historical sync progress"
              aria-valuemax={100}
              aria-valuemin={0}
              aria-valuenow={progress}
              className="h-1.5 overflow-hidden rounded-full bg-hl"
              role="progressbar"
            >
              <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}
        {sync && !syncing && sync.state !== "idle" && (
          <p className={cn("text-xs", sync.state === "failed" ? "text-ko" : "text-fg2")}>
            Last sync {label(sync.state)}
            {sync.completed_at ? ` ${fmtDateTime(sync.completed_at)}` : ""} · {fmtNum(sync.candles_upserted, 0)} candles saved
            {sync.error_count > 0 ? ` · ${sync.error_count} errors` : ""}
            {sync.failure_reason ? ` — ${sync.failure_reason}` : ""}
          </p>
        )}
        {!auth.data?.healthy && !syncing && <p className="text-xs text-wa">Log in to Fyers to sync.</p>}
        {requestError && <p className="text-xs text-ko">{requestError}</p>}
        <details className="rounded-md border border-border-soft bg-field">
          <summary className="cursor-pointer px-3 py-1.5 text-xs text-muted-text">Sync log ({sync?.logs.length ?? 0} lines)</summary>
          <div className="flex max-h-60 flex-col gap-0.5 overflow-y-auto border-t border-border-soft px-3 py-2 font-mono text-2xs">
            {sync?.logs.length ? (
              sync.logs.slice(-80).map((line, index) => (
                <span className={line.includes("ERROR") ? "text-ko" : "text-fg2"} key={`${index}-${line}`}>
                  {line}
                </span>
              ))
            ) : (
              <span className="text-muted-text">No sync output.</span>
            )}
          </div>
        </details>
      </div>
    </Panel>
  )
}

function ReconciliationPanel() {
  const runs = useReconciliationRuns(8)
  const trigger = useTriggerReconciliation()
  return (
    <Panel
      actions={
        <Button disabled={trigger.isPending} onClick={() => trigger.mutate()} size="sm" type="button" variant="outline">
          {trigger.isPending ? <Spinner data-icon="inline-start" /> : <ShieldCheckIcon data-icon="inline-start" />}
          Run now
        </Button>
      }
      description="Compares local orders, positions and fills with the broker. External trades are imported or flagged."
      flush
      title="Reconciliation"
    >
      {trigger.data && <p className="border-b border-border-soft px-4 py-1.5 text-xs text-muted-text">{trigger.data.message}</p>}
      {trigger.error instanceof Error && <p className="border-b border-border-soft px-4 py-1.5 text-xs text-ko">{trigger.error.message}</p>}
      {(runs.data?.length ?? 0) === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-text">{runs.isLoading ? "Loading…" : "No reconciliation runs yet."}</p>
      ) : (
        <table className="tbl">
          <thead>
            <tr>
              <th className="l">STARTED</th>
              <th className="l">STATUS</th>
              <th>DISCREPANCIES</th>
              <th className="l">NOTE</th>
            </tr>
          </thead>
          <tbody>
            {runs.data!.map((run) => (
              <tr key={run.id}>
                <td className="l">{fmtDateTime(run.started_at)}</td>
                <td className={cn("l capitalize", run.status === "failed" ? "text-ko" : run.status === "running" ? "text-accent" : "text-ok")}>{run.status}</td>
                <td className={run.discrepancies_found > 0 ? "text-wa" : undefined}>{run.discrepancies_found}</td>
                <td className="l max-w-[280px] truncate font-sans" title={run.error_message ?? undefined}>
                  {run.error_message ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

/** Operations: health at a glance, safety controls, rollout/policy, data jobs. */
export function DashboardOverview({ tickWorkerStatus }: DashboardOverviewProps) {
  return (
    <section className="view h-full">
      <div className="vhead">
        <div>
          <h2>Operations</h2>
          <p className="vmeta">System health, safety controls, rollout and data jobs</p>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-4">
          <section aria-label="System health" className="flex flex-col gap-2">
            <SectionLabel>Health</SectionLabel>
            <HealthRow tickWorkerStatus={tickWorkerStatus} />
          </section>
          <div className="grid gap-4 xl:grid-cols-2">
            <ControlsPanel />
            <div className="flex flex-col gap-4">
              <RolloutPanel />
              <RiskPolicyPanel />
            </div>
          </div>
          <MarketContextAdminPanel />
          <div className="grid gap-4 xl:grid-cols-2">
            <SyncPanel />
            <ReconciliationPanel />
          </div>
        </div>
      </div>
    </section>
  )
}
