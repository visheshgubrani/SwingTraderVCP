import { NavLink } from "react-router"

import { useTradeProposals } from "@/features/proposals/api"
import { productionScanRuns, useScanRuns } from "@/features/screener/api"
import { cn } from "@/lib/utils"

interface StageTabProps {
  to: string
  label: string
  count: number | null
  /** Highlight the count (something is waiting on the owner). */
  attention?: boolean
  end?: boolean
}

function StageTab({ to, label, count, attention = false, end = false }: StageTabProps) {
  return (
    <NavLink className={({ isActive }) => cn("tab", isActive && "on")} end={end} to={to}>
      {label}
      {count !== null && <span className={cn("tcount", attention && count > 0 && "attn")}>{count}</span>}
    </NavLink>
  )
}

/** Module tabs docked at the bottom of the workstation — the app's only navigation. */
export function StageNav({
  lastSearches,
  positionsCount,
  orderIntentsCount,
}: {
  /** Last query string per module path, so each tab returns to where you left it. */
  lastSearches: Record<string, string>
  positionsCount: number
  orderIntentsCount: number
}) {
  const scanRuns = useScanRuns()
  const pending = useTradeProposals("pending_approval", { limit: 200 })

  const latestRun = productionScanRuns(scanRuns.data).find((run) => run.status === "succeeded")
  const scannerCount = latestRun?.passing_count ?? null
  const pendingCount = pending.data?.length ?? null

  const tabs: StageTabProps[] = [
    { to: "/", label: "Chart", count: null, end: true },
    { to: "/scanner", label: "Scanner", count: scannerCount },
    { to: "/proposals", label: "Proposals", count: pendingCount, attention: true },
    { to: "/positions", label: "Positions", count: positionsCount },
    { to: "/orders", label: "Orders", count: orderIntentsCount },
    { to: "/tradebook", label: "Tradebook", count: null },
    { to: "/journal", label: "Journal", count: null },
    { to: "/fundamentals", label: "Fundamentals", count: null },
    { to: "/operations", label: "Operations", count: null },
  ]

  return (
    <nav aria-label="Workspace modules" className="tabs">
      {tabs.map((tab) => (
        <StageTab key={tab.label} {...tab} to={`${tab.to}${lastSearches[tab.to] ?? ""}`} />
      ))}
    </nav>
  )
}
