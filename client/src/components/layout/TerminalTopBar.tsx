import { useEffect, useState } from "react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useAppAuth } from "@/features/auth/AuthContext"
import { useAuthStatus, useStartFyersLogin } from "@/features/auth/api"
import { useKillSwitch, useSetKillSwitch } from "@/features/admin/api"
import { useP10Rollout, usePaperPortfolio } from "@/features/proposals/api"
import { useExecutionStatus } from "@/features/trade/api"
import { useMarketData } from "@/lib/MarketWSContext"
import { cn } from "@/lib/utils"
import { fmtAmount, isNseOpen } from "@/lib/format"
import { GlobalSearch } from "@/components/terminal/GlobalSearch"
import { Spinner } from "@/components/ui/spinner"

const STAGE_AVATAR: Record<string, string> = {
  shadow: "SH",
  paper: "P10",
  reduced_live: "RL",
  full_live: "LV",
}

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])
  return now
}

/** Terminal top bar: brand · global search · account/feed/system status. */
export function TerminalTopBar() {
  const { logout } = useAppAuth()
  const authStatus = useAuthStatus()
  const startLogin = useStartFyersLogin()
  const killSwitch = useKillSwitch()
  const setKillSwitch = useSetKillSwitch()
  const execution = useExecutionStatus()
  const rollout = useP10Rollout()
  const stage = rollout.data?.stage
  const isPaper = (execution.data?.execution_mode ?? "paper") === "paper"
  const paper = usePaperPortfolio(isPaper && stage === "paper")
  const { tickWorkerStatus, readyState } = useMarketData()
  const now = useClock()

  const [killDialogOpen, setKillDialogOpen] = useState(false)
  const killActive = killSwitch.data?.enabled ?? true

  const feedLive =
    readyState === 1 && (tickWorkerStatus?.status ?? "").toLowerCase().includes("run")
  const marketOpen = isNseOpen(now)
  const healthy = authStatus.data?.healthy ?? false

  const handleKillSwitchChange = async () => {
    const enabled = !killActive
    try {
      await setKillSwitch.mutateAsync({
        enabled,
        reason: enabled
          ? "Human engaged the global automation kill switch from the UI."
          : "Human explicitly resumed automated order handling from the UI.",
      })
      setKillDialogOpen(false)
    } catch {
      // Dialog stays open; mutation error is rendered below.
    }
  }

  const stageLabel = (stage ?? (execution.data ? undefined : "shadow"))?.toUpperCase()

  return (
    <header className="tb">
      {/* Brand */}
      <div className="brand">
        <svg aria-hidden="true" className="brand-ic" fill="none" viewBox="0 0 24 24">
          <path d="M6.5 21V3M5 9.6h3v6.8H5zM13 21V7.4M11.5 12h3v6h-3zM19.5 21V9M18 13.4h3v5h-3z" strokeLinejoin="round" />
        </svg>
        <span className="bname">VCP TRADER</span>
        <span className="bchip">CORE</span>
      </div>

      <GlobalSearch />

      {/* Right: status chips + system */}
      <div className="tbright">
        <span
          className="chip chip-feed hidden min-[560px]:inline-flex"
          title={`Tick worker ${tickWorkerStatus?.status ?? "unknown"} · ${tickWorkerStatus?.symbol_count ?? 0} symbols · ${tickWorkerStatus?.timestamp ?? "no heartbeat"}`}
        >
          <i className={cn("dot", feedLive ? "text-ok" : marketOpen ? "text-wa" : "")} />
          {feedLive ? "FEED LIVE" : "FEED STANDBY"}
        </span>

        <span
          className="chip chip-acc"
          title={`${stageLabel ?? "—"} · ${execution.data?.execution_mode ?? "paper"} account${paper.data ? " · paper cash ledger" : ""}`}
        >
          {stageLabel ?? "—"} · {execution.data?.execution_mode.toUpperCase() ?? "PAPER"}
          {paper.data ? (
            <span className="mono">{fmtAmount(paper.data.cash_available)}</span>
          ) : (
            execution.data?.execution_mode === "live" && (
              <span className="mono">LIVE ARMED {execution.data.live_order_placement_enabled ? "ON" : "OFF"}</span>
            )
          )}
        </span>

        <span className="chip mono hidden min-[1180px]:inline-flex">
          <i className={cn("dot", marketOpen ? "text-ok" : "text-muted-text")} />
          {now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })} IST
        </span>

        {/* Fyers auth */}
        <button
          className="btn btn-ghost !h-[24px] !px-2 !text-[10.5px]"
          disabled={authStatus.isLoading || startLogin.isPending}
          onClick={() => startLogin.mutate()}
          title={authStatus.data?.expires_at ? `Token expires ${new Date(authStatus.data.expires_at).toLocaleString("en-IN")}` : "Authenticate with Fyers"}
          type="button"
        >
          {startLogin.isPending ? (
            <Spinner data-icon="inline-start" />
          ) : (
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                healthy ? "bg-ok" : authStatus.data ? "bg-ko" : "bg-wa",
              )}
              aria-hidden="true"
            />
          )}
          {healthy ? "FYERS" : "LOGIN FYERS"}
        </button>

        {/* Kill switch */}
        <AlertDialog open={killDialogOpen} onOpenChange={setKillDialogOpen}>
          <button
            className={cn("chip", killActive ? "chip-danger" : "chip-acc", "cursor-pointer")}
            onClick={() => setKillDialogOpen(true)}
            title={killActive ? "Kill switch engaged — no automated orders" : "Automation engine enabled"}
            type="button"
          >
            <i className={cn("dot", killActive ? "" : "text-ok")} />
            {killSwitch.isLoading || setKillSwitch.isPending
              ? "…"
              : killActive
                ? "KILL ON"
                : "ENGINE ON"}
          </button>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogMedia>{killActive ? "⛔" : "✅"}</AlertDialogMedia>
              <AlertDialogTitle>
                {killActive ? "Resume automated orders?" : "Engage the global kill switch?"}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {killActive
                  ? "This re-enables entry confirmation and automated exits."
                  : "This blocks every new automated entry and exit intent. It does not flatten positions and is not a substitute for being flat."}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {setKillSwitch.error instanceof Error && (
              <p className="text-sm text-destructive">{setKillSwitch.error.message}</p>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={setKillSwitch.isPending}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={setKillSwitch.isPending}
                onClick={() => void handleKillSwitchChange()}
                variant={killActive ? "default" : "destructive"}
              >
                {setKillSwitch.isPending && <Spinner data-icon="inline-start" />}
                {killActive ? "Resume automation" : "Engage kill switch"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* Sign out */}
        <button
          aria-label="Sign out of Workstation"
          className="btn btn-ghost !h-[26px] !px-2"
          onClick={() => void logout()}
          title="Sign out of Workstation"
          type="button"
        >
          <svg aria-hidden="true" className="size-3.5" fill="none" viewBox="0 0 24 24">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
          </svg>
        </button>

        <span className="avatar" title={`${stageLabel ?? "—"} · ${execution.data?.execution_mode ?? "paper"} account`}>
          {STAGE_AVATAR[stage ?? ""] ?? (stage ? stage.slice(0, 2).toUpperCase() : "OP")}
        </span>
      </div>
    </header>
  )
}
